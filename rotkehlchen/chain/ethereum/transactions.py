import logging
from typing import TYPE_CHECKING, List, Optional, Tuple

from rotkehlchen.db.ethtx import DBEthTx
from rotkehlchen.db.filtering import ETHTransactionsFilterQuery
from rotkehlchen.db.ranges import DBQueryRanges
from rotkehlchen.errors.misc import RemoteError
from rotkehlchen.logging import RotkehlchenLogsAdapter
from rotkehlchen.types import (
    ChecksumEthAddress,
    EthereumTransaction,
    EVMTxHash,
    Timestamp,
    deserialize_evm_tx_hash,
)
from rotkehlchen.utils.misc import ts_now
from rotkehlchen.utils.mixins.lockable import LockableQueryMixIn, protect_with_lock

if TYPE_CHECKING:
    from rotkehlchen.chain.ethereum.manager import EthereumManager
    from rotkehlchen.chain.ethereum.structures import EthereumTxReceipt
    from rotkehlchen.db.dbhandler import DBHandler


logger = logging.getLogger(__name__)
log = RotkehlchenLogsAdapter(logger)


class EthTransactions(LockableQueryMixIn):

    def __init__(
            self,
            ethereum: 'EthereumManager',
            database: 'DBHandler',
    ) -> None:
        super().__init__()
        self.ethereum = ethereum
        self.database = database

    def single_address_query_transactions(
            self,
            address: ChecksumEthAddress,
            start_ts: Timestamp,
            end_ts: Timestamp,
    ) -> None:
        """Only queries new transactions and adds them to the DB

        This is our attempt to identify as many transactions related to the address
        as possible. This unfortunately at the moment depends on etherscan as it's
        the only open indexing service for "appearances" of an address.

        Trueblocks ... we need you.
        """
        ranges = DBQueryRanges(self.database)
        ranges_to_query = ranges.get_location_query_ranges(
            location_string=f'ethtxs_{address}',
            start_ts=start_ts,
            end_ts=end_ts,
        )
        new_transactions = []
        dbethtx = DBEthTx(self.database)
        for query_start_ts, query_end_ts in ranges_to_query:
            try:
                new_transactions.extend(self.ethereum.etherscan.get_transactions(
                    account=address,
                    from_ts=query_start_ts,
                    to_ts=query_end_ts,
                    action='txlist',
                ))
            except RemoteError as e:
                self.ethereum.msg_aggregator.add_error(
                    f'Got error "{str(e)}" while querying ethereum transactions '
                    f'from Etherscan. Transactions not added to the DB '
                    f'address: {address} '
                    f'from_ts: {query_start_ts} '
                    f'to_ts: {query_end_ts} ',
                )

        # add new transactions to the DB
        if new_transactions != []:
            dbethtx.add_ethereum_transactions(new_transactions, relevant_address=address)

        # add internal transactions, after normal ones so they are already in DB
        self._get_internal_transactions_for_ranges(address=address, ranges_to_query=ranges_to_query)  # noqa: E501

        # and now detect ERC20 events thanks to etherscan and get their transactions
        self._get_erc20_transfers_for_ranges(address=address, ranges_to_query=ranges_to_query)

        # finally also set the last queried timestamps for the address
        ranges.update_used_query_range(
            location_string=f'ethtxs_{address}',
            start_ts=start_ts,
            end_ts=end_ts,
            ranges_to_query=ranges_to_query,
        )

    @protect_with_lock()
    def query(
            self,
            filter_query: ETHTransactionsFilterQuery,
            has_premium: bool = False,
            only_cache: bool = False,
    ) -> Tuple[List[EthereumTransaction], int]:
        """Queries for all transactions of an ethereum address or of all addresses.

        Returns a list of all transactions filtered and sorted according to the parameters.

        May raise:
        - RemoteError if etherscan is used and there is a problem with reaching it or
        with parsing the response.
        - pysqlcipher3.dbapi2.OperationalError if the SQL query fails due to
        invalid filtering arguments.
        """
        query_addresses = filter_query.addresses

        if query_addresses is not None:
            accounts = query_addresses
        else:
            accounts = self.database.get_blockchain_accounts().eth

        if only_cache is False:
            f_from_ts = filter_query.from_ts
            f_to_ts = filter_query.to_ts
            from_ts = Timestamp(0) if f_from_ts is None else f_from_ts
            to_ts = ts_now() if f_to_ts is None else f_to_ts
            for address in accounts:
                self.single_address_query_transactions(
                    address=address,
                    start_ts=from_ts,
                    end_ts=to_ts,
                )

        dbethtx = DBEthTx(self.database)
        return dbethtx.get_ethereum_transactions_and_limit_info(
            filter_=filter_query,
            has_premium=has_premium,
        )

    def _get_internal_transactions_for_ranges(
            self,
            address: ChecksumEthAddress,
            ranges_to_query: List[Tuple[Timestamp, Timestamp]],
    ) -> None:
        """Queries etherscan for all internal transactions of address in the given ranges.

        If any internal transactions are found, they are added in the DB
        """
        new_internal_txs = []
        dbethtx = DBEthTx(self.database)
        for query_start_ts, query_end_ts in ranges_to_query:
            try:
                new_internal_txs.extend(self.ethereum.etherscan.get_transactions(
                    account=address,
                    from_ts=query_start_ts,
                    to_ts=query_end_ts,
                    action='txlistinternal',
                ))
            except RemoteError as e:
                self.ethereum.msg_aggregator.add_error(
                    f'Got error "{str(e)}" while querying internal ethereum transactions '
                    f'from Etherscan. Transactions not added to the DB '
                    f'address: {address} '
                    f'from_ts: {query_start_ts} '
                    f'to_ts: {query_end_ts} ',
                )

        # add new internal transactions to the DB
        if new_internal_txs != []:
            for internal_tx in new_internal_txs:
                # make sure all internal transaction parent transactions are in the DB
                result = dbethtx.get_ethereum_transactions(
                    ETHTransactionsFilterQuery.make(tx_hash=internal_tx.parent_tx_hash),
                    has_premium=True,  # ignore limiting here
                )
                if len(result) != 0:
                    continue  # already got that transaction

                transaction = self.ethereum.get_transaction_by_hash(internal_tx.parent_tx_hash)
                # add the parent transaction to the DB
                dbethtx.add_ethereum_transactions([transaction], relevant_address=address)

            # add all new internal txs to the DB
            dbethtx.add_ethereum_internal_transactions(new_internal_txs, relevant_address=address)

    def _get_erc20_transfers_for_ranges(
            self,
            address: ChecksumEthAddress,
            ranges_to_query: List[Tuple[Timestamp, Timestamp]],
    ) -> None:
        """Queries etherscan for all erc20 transfers of address in the given ranges.

        If any transfers are found, they are added in the DB
        """
        dbethtx = DBEthTx(self.database)
        erc20_tx_hashes = set()
        for query_start_ts, query_end_ts in ranges_to_query:
            try:
                erc20_tx_hashes.update(self.ethereum.etherscan.get_transactions(
                    account=address,
                    from_ts=query_start_ts,
                    to_ts=query_end_ts,
                    action='tokentx',
                ))
            except RemoteError as e:
                self.ethereum.msg_aggregator.add_error(
                    f'Got error "{str(e)}" while querying token transactions'
                    f'from Etherscan. Transactions not added to the DB '
                    f'address: {address} '
                    f'from_ts: {query_start_ts} '
                    f'to_ts: {query_end_ts} ',
                )

        # and add them to the DB
        for tx_hash in erc20_tx_hashes:
            tx_hash_bytes = deserialize_evm_tx_hash(tx_hash)
            result = dbethtx.get_ethereum_transactions(
                ETHTransactionsFilterQuery.make(tx_hash=tx_hash_bytes),
                has_premium=True,  # ignore limiting here
            )
            if len(result) != 0:
                continue  # already got that transaction

            transaction = self.ethereum.get_transaction_by_hash(tx_hash_bytes)
            dbethtx.add_ethereum_transactions([transaction], relevant_address=address)

    def get_or_query_transaction_receipt(
            self,
            tx_hash: EVMTxHash,
    ) -> 'EthereumTxReceipt':
        """
        Gets the receipt from the DB if it exists. If not queries the chain for it,
        saves it in the DB and then returns it.

        Also if the actual transaction does not exist in the DB it queries it and saves it there.

        May raise:

        - DeserializationError
        - RemoteError if the transaction hash can't be found in any of the connected nodes
        """
        dbethtx = DBEthTx(self.database)
        # If the transaction is not in the DB then query it and add it
        result = dbethtx.get_ethereum_transactions(
            filter_=ETHTransactionsFilterQuery.make(tx_hash=tx_hash),
            has_premium=True,  # we don't need any limiting here
        )
        if len(result) == 0:
            transaction = self.ethereum.get_transaction_by_hash(tx_hash)
            dbethtx.add_ethereum_transactions([transaction], relevant_address=None)
            ranges_to_query = [(transaction.timestamp, transaction.timestamp)]
            self._get_internal_transactions_for_ranges(address=transaction.from_address, ranges_to_query=ranges_to_query)  # noqa: E501
            self._get_erc20_transfers_for_ranges(address=transaction.from_address, ranges_to_query=ranges_to_query)  # noqa: E501

        tx_receipt = dbethtx.get_receipt(tx_hash)
        if tx_receipt is not None:
            return tx_receipt

        # not in the DB, so we need to query the chain for it
        tx_receipt_data = self.ethereum.get_transaction_receipt(tx_hash=tx_hash)
        dbethtx.add_receipt_data(tx_receipt_data)
        tx_receipt = dbethtx.get_receipt(tx_hash)
        return tx_receipt  # type: ignore  # tx_receipt was just added in the DB so should be there

    def get_receipts_for_transactions_missing_them(self, limit: Optional[int] = None) -> None:
        """
        Searches the database for up to `limit` transactions that have no corresponding receipt
        and for each one of them queries the receipt and saves it in the DB.

        It's protected by a lock to not enter the same code twice
        (i.e. from periodic tasks and from pnl report history events gathering)
        """
        with self.ethereum.receipts_query_lock:
            dbethtx = DBEthTx(self.database)
            hash_results = dbethtx.get_transaction_hashes_no_receipt(
                tx_filter_query=None,
                limit=limit,
            )

            if len(hash_results) == 0:
                return  # nothing to do

            for entry in hash_results:
                tx_receipt_data = self.ethereum.get_transaction_receipt(tx_hash=entry)
                dbethtx.add_receipt_data(tx_receipt_data)
