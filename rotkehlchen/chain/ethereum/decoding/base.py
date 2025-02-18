from typing import TYPE_CHECKING, Optional, Tuple

from rotkehlchen.accounting.structures.balance import Balance
from rotkehlchen.accounting.structures.base import (
    HistoryBaseEntry,
    HistoryEventSubType,
    HistoryEventType,
)
from rotkehlchen.types import ChecksumEthAddress

if TYPE_CHECKING:
    from rotkehlchen.db.dbhandler import DBHandler

from rotkehlchen.assets.asset import EthereumToken
from rotkehlchen.chain.ethereum.structures import EthereumTxReceiptLog
from rotkehlchen.chain.ethereum.utils import token_normalized_value
from rotkehlchen.fval import FVal
from rotkehlchen.types import EthereumTransaction, Location
from rotkehlchen.utils.misc import hex_or_bytes_to_address, hex_or_bytes_to_int, ts_sec_to_ms

from .constants import NAUGHTY_ERC721


class BaseDecoderTools():
    """A class that keeps a common state and offers some common decoding functionality"""

    def __init__(self, database: 'DBHandler') -> None:
        self.database = database
        self.tracked_accounts = self.database.get_blockchain_accounts()
        self.sequence_counter = 0

    def reset_sequence_counter(self) -> None:
        self.sequence_counter = 0

    def get_next_sequence_counter(self) -> int:
        """Returns current counter and also increases it.
        Meant to be called for all transaction events that do not have a corresponding log index"""
        value = self.sequence_counter
        self.sequence_counter += 1
        return value

    def get_sequence_index(self, tx_log: EthereumTxReceiptLog) -> int:
        """Get the value that should go for this event's sequence index

        This function exists to calculate the index bases on the pre-calculated
        sequence index and the event's log index"""
        return self.sequence_counter + tx_log.log_index

    def refresh_tracked_accounts(self) -> None:
        self.tracked_accounts = self.database.get_blockchain_accounts()

    def is_tracked(self, adddress: ChecksumEthAddress) -> bool:
        return adddress in self.tracked_accounts.eth

    def decode_direction(
            self,
            from_address: ChecksumEthAddress,
            to_address: Optional[ChecksumEthAddress],
            set_verbs: Optional[Tuple[str, str]] = None,
            set_counterparty: Optional[str] = None,
    ) -> Optional[Tuple[HistoryEventType, str, str, str]]:
        tracked_from = from_address in self.tracked_accounts.eth
        tracked_to = to_address in self.tracked_accounts.eth
        if not tracked_from and not tracked_to:
            return None

        if tracked_from and tracked_to:
            event_type = HistoryEventType.TRANSFER
            location_label = from_address
            counterparty = to_address if not set_counterparty else set_counterparty
            verb = 'Send' if not set_verbs else set_verbs[0]
        elif tracked_from:
            event_type = HistoryEventType.SPEND
            location_label = from_address
            counterparty = to_address if not set_counterparty else set_counterparty
            verb = 'Send' if not set_verbs else set_verbs[0]
        else:  # can only be tracked_to
            event_type = HistoryEventType.RECEIVE
            location_label = to_address  # type: ignore  # to_address can't be None here
            counterparty = from_address if not set_counterparty else set_counterparty
            verb = 'Receive' if not set_verbs else set_verbs[1]

        return event_type, location_label, counterparty, verb  # type: ignore

    def decode_erc20_721_transfer(
            self,
            token: EthereumToken,
            tx_log: EthereumTxReceiptLog,
            transaction: EthereumTransaction,
            set_verbs: Optional[Tuple[str, str]] = None,
            set_counterparty: Optional[str] = None,
    ) -> Optional[HistoryBaseEntry]:
        """
        Caller should know this is a transfer of either an ERC20 or an ERC721 token.
        Call this method to decode it.

        May raise:
        - DeserializationError
        - ConversionError
        """
        if token.ethereum_address in NAUGHTY_ERC721:
            token_type = 'erc721'
        elif len(tx_log.topics) == 3:  # typical ERC20 has 2 indexed args
            token_type = 'erc20'
        elif len(tx_log.topics) == 4:  # typical ERC721 has 3 indexed args
            token_type = 'erc721'
        else:
            return None

        from_address = hex_or_bytes_to_address(tx_log.topics[1])
        to_address = hex_or_bytes_to_address(tx_log.topics[2])
        direction_result = self.decode_direction(
            from_address=from_address,
            to_address=to_address,
            set_verbs=set_verbs,
            set_counterparty=set_counterparty,
        )
        if direction_result is None:
            return None

        event_type, location_label, counterparty, verb = direction_result
        event_subtype = HistoryEventSubType.NONE
        amount_raw_or_token_id = hex_or_bytes_to_int(tx_log.data)
        if token_type == 'erc20':
            amount = token_normalized_value(token_amount=amount_raw_or_token_id, token=token)
            if event_type == HistoryEventType.SPEND:
                notes = f'{verb} {amount} {token.symbol} from {location_label} to {counterparty}'
            else:
                notes = f'{verb} {amount} {token.symbol} from {counterparty} to {location_label}'
        else:
            token_id = hex_or_bytes_to_int(tx_log.data)
            amount = FVal(1)
            if event_type == HistoryEventType.SPEND:
                notes = f'{verb} {token.name} with id {token_id} from {location_label} to {counterparty}'  # noqa: E501
            else:
                notes = f'{verb} {token.name} with id {token_id} from {counterparty} to {location_label}'  # noqa: E501

        return HistoryBaseEntry(
            event_identifier=transaction.tx_hash.hex(),
            sequence_index=self.get_sequence_index(tx_log),
            timestamp=ts_sec_to_ms(transaction.timestamp),
            location=Location.BLOCKCHAIN,
            location_label=location_label,
            asset=token,
            balance=Balance(amount=amount),
            notes=notes,
            event_type=event_type,
            event_subtype=event_subtype,
            counterparty=counterparty,
        )
