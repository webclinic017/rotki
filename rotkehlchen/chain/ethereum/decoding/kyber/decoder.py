from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from rotkehlchen.accounting.structures.base import (
    HistoryBaseEntry,
    HistoryEventSubType,
    HistoryEventType,
    get_tx_event_type_identifier,
)
from rotkehlchen.assets.asset import Asset
from rotkehlchen.chain.ethereum.decoding.interfaces import DecoderInterface
from rotkehlchen.chain.ethereum.decoding.structures import (
    ActionItem,
    TxEventSettings,
    TxMultitakeTreatment,
)
from rotkehlchen.chain.ethereum.decoding.utils import maybe_reshuffle_events
from rotkehlchen.chain.ethereum.structures import EthereumTxReceiptLog
from rotkehlchen.chain.ethereum.types import string_to_ethereum_address
from rotkehlchen.chain.ethereum.utils import asset_normalized_value, ethaddress_to_asset
from rotkehlchen.fval import FVal
from rotkehlchen.types import ChecksumEthAddress, EthereumTransaction
from rotkehlchen.user_messages import MessagesAggregator
from rotkehlchen.utils.misc import hex_or_bytes_to_address, hex_or_bytes_to_int

if TYPE_CHECKING:
    from rotkehlchen.accounting.pot import AccountingPot
    from rotkehlchen.chain.ethereum.decoding.base import BaseDecoderTools
    from rotkehlchen.chain.ethereum.manager import EthereumManager

KYBER_TRADE_LEGACY = b'\xf7$\xb4\xdff\x17G6\x12\xb5=\x7f\x88\xec\xc6\xea\x980t\xb3\t`\xa0I\xfc\xd0e\x7f\xfe\x80\x80\x83'  # noqa: E501
KYBER_LEGACY_CONTRACT = string_to_ethereum_address('0x9ae49C0d7F8F9EF4B864e004FE86Ac8294E20950')
KYBER_LEGACY_CONTRACT_MIGRATED = string_to_ethereum_address('0x65bF64Ff5f51272f729BDcD7AcFB00677ced86Cd')  # noqa: E501
KYBER_LEGACY_CONTRACT_UPGRADED = string_to_ethereum_address('0x9AAb3f75489902f3a48495025729a0AF77d4b11e')  # noqa: E501

CPT_KYBER = 'kyber legacy'


def _legacy_contracts_basic_info(tx_log: EthereumTxReceiptLog) -> Tuple[ChecksumEthAddress, Optional[Asset], Optional[Asset]]:  # noqa: E501
    """
    Returns:
    - address of the sender
    - source token (can be none)
    - destination token (can be none)
    May raise:
    - DeserializationError when using hex_or_bytes_to_address
    """
    sender = hex_or_bytes_to_address(tx_log.topics[1])
    source_token_address = hex_or_bytes_to_address(tx_log.data[:32])
    destination_token_address = hex_or_bytes_to_address(tx_log.data[32:64])

    source_token = ethaddress_to_asset(source_token_address)
    destination_token = ethaddress_to_asset(destination_token_address)
    return sender, source_token, destination_token


def _maybe_update_events_legacy_contrats(
    decoded_events: List[HistoryBaseEntry],
    sender: ChecksumEthAddress,
    source_token: Asset,
    destination_token: Asset,
    spent_amount: FVal,
    return_amount: FVal,
) -> None:
    """
    Use the information from a trade transaction to modify the HistoryEvents from receive/send to
    trade if the conditions are correct.
    """
    in_event = out_event = None
    for event in decoded_events:
        if event.event_type == HistoryEventType.SPEND and event.location_label == sender and event.asset == source_token and event.balance.amount == spent_amount:  # noqa: E501
            event.event_type = HistoryEventType.TRADE
            event.event_subtype = HistoryEventSubType.SPEND
            event.counterparty = CPT_KYBER
            event.notes = f'Swap {event.balance.amount} {event.asset.symbol} in kyber'
            out_event = event
        elif event.event_type == HistoryEventType.RECEIVE and event.location_label == sender and event.balance.amount == return_amount and destination_token == event.asset:  # noqa: E501
            event.event_type = HistoryEventType.TRADE
            event.event_subtype = HistoryEventSubType.RECEIVE
            event.counterparty = CPT_KYBER
            event.notes = f'Receive {event.balance.amount} {event.asset.symbol} from kyber swap'  # noqa: E501
            in_event = event

        maybe_reshuffle_events(out_event=out_event, in_event=in_event)


class KyberDecoder(DecoderInterface):  # lgtm[py/missing-call-to-init]
    def __init__(  # pylint: disable=super-init-not-called
            self,
            ethereum_manager: 'EthereumManager',
            base_tools: 'BaseDecoderTools',
            msg_aggregator: MessagesAggregator,
    ) -> None:
        self.ethereum_manager = ethereum_manager
        self.base = base_tools
        self.msg_aggregator = msg_aggregator

    def _decode_legacy_trade(  # pylint: disable=no-self-use
        self,
        tx_log: EthereumTxReceiptLog,
        transaction: EthereumTransaction,  # pylint: disable=unused-argument
        decoded_events: List[HistoryBaseEntry],
        all_logs: List[EthereumTxReceiptLog],  # pylint: disable=unused-argument
        action_items: Optional[List[ActionItem]],  # pylint: disable=unused-argument
    ) -> Tuple[Optional[HistoryBaseEntry], Optional[ActionItem]]:
        if tx_log.topics[0] == KYBER_TRADE_LEGACY:
            return None, None

        sender, source_token, destination_token = _legacy_contracts_basic_info(tx_log)
        if source_token is None or destination_token is None:
            return None, None

        spent_amount_raw = hex_or_bytes_to_int(tx_log.data[64:96])
        return_amount_raw = hex_or_bytes_to_int(tx_log.data[96:128])
        spent_amount = asset_normalized_value(amount=spent_amount_raw, asset=source_token)
        return_amount = asset_normalized_value(amount=return_amount_raw, asset=destination_token)
        _maybe_update_events_legacy_contrats(
            decoded_events=decoded_events,
            sender=sender,
            source_token=source_token,
            destination_token=destination_token,
            spent_amount=spent_amount,
            return_amount=return_amount,
        )

        return None, None

    def _decode_legacy_upgraded_trade(  # pylint: disable=no-self-use
        self,
        tx_log: EthereumTxReceiptLog,
        transaction: EthereumTransaction,  # pylint: disable=unused-argument
        decoded_events: List[HistoryBaseEntry],
        all_logs: List[EthereumTxReceiptLog],  # pylint: disable=unused-argument
        action_items: Optional[List[ActionItem]],  # pylint: disable=unused-argument
    ) -> Tuple[Optional[HistoryBaseEntry], Optional[ActionItem]]:
        if tx_log.topics[0] != KYBER_TRADE_LEGACY:
            return None, None

        sender, source_token, destination_token = _legacy_contracts_basic_info(tx_log)
        if source_token is None or destination_token is None:
            return None, None

        spent_amount_raw = hex_or_bytes_to_int(tx_log.data[96:128])
        return_amount_raw = hex_or_bytes_to_int(tx_log.data[128:160])
        spent_amount = asset_normalized_value(amount=spent_amount_raw, asset=source_token)
        return_amount = asset_normalized_value(amount=return_amount_raw, asset=destination_token)
        _maybe_update_events_legacy_contrats(
            decoded_events=decoded_events,
            sender=sender,
            source_token=source_token,
            destination_token=destination_token,
            spent_amount=spent_amount,
            return_amount=return_amount,
        )

        return None, None

    # -- DecoderInterface methods

    def addresses_to_decoders(self) -> Dict[ChecksumEthAddress, Tuple[Any, ...]]:
        return {
            KYBER_LEGACY_CONTRACT: (self._decode_legacy_trade,),
            KYBER_LEGACY_CONTRACT_MIGRATED: (self._decode_legacy_trade,),
            KYBER_LEGACY_CONTRACT_UPGRADED: (self._decode_legacy_upgraded_trade,),
        }

    def counterparties(self) -> List[str]:
        return [CPT_KYBER]

    def event_settings(self, pot: 'AccountingPot') -> Dict[str, TxEventSettings]:  # pylint: disable=unused-argument  # noqa: E501
        """Being defined at function call time is fine since this function is called only once"""
        return {
            get_tx_event_type_identifier(HistoryEventType.TRADE, HistoryEventSubType.SPEND, CPT_KYBER): TxEventSettings(  # noqa: E501
                taxable=True,
                count_entire_amount_spend=False,
                count_cost_basis_pnl=True,
                method='spend',
                take=2,
                multitake_treatment=TxMultitakeTreatment.SWAP,
            ),
        }
