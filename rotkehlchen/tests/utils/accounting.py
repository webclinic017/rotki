import csv
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from rotkehlchen.accounting.export.csv import CSV_INDEX_OFFSET, FILENAME_ALL_CSV
from rotkehlchen.accounting.mixins.event import AccountingEventMixin, AccountingEventType
from rotkehlchen.accounting.pnl import PNL, PnlTotals
from rotkehlchen.accounting.structures.processed_event import ProcessedAccountingEvent
from rotkehlchen.constants import ZERO
from rotkehlchen.constants.assets import A_BTC, A_ETH, A_EUR
from rotkehlchen.db.reports import DBAccountingReports, ReportDataFilterQuery
from rotkehlchen.exchanges.data_structures import Trade
from rotkehlchen.fval import FVal
from rotkehlchen.types import AssetAmount, Fee, Location, Price, Timestamp, TradeType
from rotkehlchen.utils.version_check import get_current_version

if TYPE_CHECKING:
    from rotkehlchen.accounting.accountant import Accountant
    from rotkehlchen.db.dbhandler import DBHandler
    from rotkehlchen.rotkehlchen import Rotkehlchen
    from rotkehlchen.tests.fixtures.google import GoogleService


history1 = [
    Trade(
        timestamp=Timestamp(1446979735),
        location=Location.EXTERNAL,
        base_asset=A_BTC,
        quote_asset=A_EUR,
        trade_type=TradeType.BUY,
        amount=AssetAmount(FVal(82)),
        rate=Price(FVal('268.678317859')),
        fee=None,
        fee_currency=None,
        link=None,
    ), Trade(
        timestamp=Timestamp(1446979735),
        location=Location.EXTERNAL,
        base_asset=A_ETH,
        quote_asset=A_EUR,
        trade_type=TradeType.BUY,
        amount=AssetAmount(FVal(1450)),
        rate=Price(FVal('0.2315893')),
        fee=None,
        fee_currency=None,
        link=None,
    ), Trade(
        timestamp=Timestamp(1473505138),  # cryptocompare hourly BTC/EUR price: 556.435
        location=Location.POLONIEX,
        base_asset=A_ETH,  # cryptocompare hourly ETH/EUR price: 10.36
        quote_asset=A_BTC,
        trade_type=TradeType.BUY,
        amount=AssetAmount(FVal(50)),
        rate=Price(FVal('0.01858275')),
        fee=Fee(FVal('0.06999999999999999')),
        fee_currency=A_ETH,
        link=None,
    ), Trade(
        timestamp=Timestamp(1475042230),  # cryptocompare hourly BTC/EUR price: 537.805
        location=Location.POLONIEX,
        base_asset=A_ETH,  # cryptocompare hourly ETH/EUR price: 11.925
        quote_asset=A_BTC,
        trade_type=TradeType.SELL,
        amount=AssetAmount(FVal(25)),
        rate=Price(FVal('0.02209898')),
        fee=Fee(FVal('0.00082871175')),
        fee_currency=A_BTC,
        link=None,
    ),
]


def _get_pnl_report_after_processing(
        report_id: int,
        database: 'DBHandler',
) -> Tuple[Dict[str, Any], List[ProcessedAccountingEvent]]:
    dbpnl = DBAccountingReports(database)
    report = dbpnl.get_reports(report_id=report_id, with_limit=False)[0][0]
    events = dbpnl.get_report_data(
        filter_=ReportDataFilterQuery.make(report_id=1),
        with_limit=False,
    )[0]
    return report, events


def accounting_create_and_process_history(
        rotki: 'Rotkehlchen',
        start_ts: Timestamp,
        end_ts: Timestamp,
) -> Tuple[Dict[str, Any], List[ProcessedAccountingEvent]]:
    report_id, error_or_empty = rotki.process_history(start_ts=start_ts, end_ts=end_ts)
    assert error_or_empty == ''
    return _get_pnl_report_after_processing(report_id=report_id, database=rotki.data.db)


def accounting_history_process(
        accountant: 'Accountant',
        start_ts: Timestamp,
        end_ts: Timestamp,
        history_list: List[AccountingEventMixin],
) -> Tuple[Dict[str, Any], List[ProcessedAccountingEvent]]:
    report_id = accountant.process_history(
        start_ts=start_ts,
        end_ts=end_ts,
        events=history_list,
    )
    return _get_pnl_report_after_processing(report_id=report_id, database=accountant.csvexporter.database)  # noqa: E501


def check_pnls_and_csv(
        accountant: 'Accountant',
        expected_pnls: PnlTotals,
        google_service: Optional['GoogleService'] = None,
) -> None:
    pnls = accountant.pots[0].pnls
    assert_pnl_totals_close(expected=expected_pnls, got=pnls)
    assert_csv_export(accountant, expected_pnls, google_service)
    # also check the totals
    assert pnls.taxable.is_close(expected_pnls.taxable)
    assert pnls.free.is_close(expected_pnls.free)


def assert_pnl_totals_close(expected: PnlTotals, got: PnlTotals) -> None:
    # ignore prefork acquisitions for these tests
    got.pop(AccountingEventType.PREFORK_ACQUISITION)

    assert len(expected) == len(got)
    for event_type, expected_pnl in expected.items():
        assert expected_pnl.free.is_close(got[event_type].free)
        assert expected_pnl.taxable.is_close(got[event_type].taxable)


def _check_boolean_settings(row: Dict[str, Any], accountant: 'Accountant'):
    """Check boolean settings are exported correctly to the spreadsheet CSV"""
    booleans = ('include_crypto2crypto', 'include_gas_costs', 'account_for_assets_movements', 'calculate_past_cost_basis')  # noqa: E501

    for setting in booleans:
        if row['free_amount'] == setting:
            assert row['taxable_amount'] == str(getattr(accountant.pots[0].settings, setting))
            break


def _check_summaries_row(row: Dict[str, Any], accountant: 'Accountant'):
    if row['free_amount'] == 'rotki version':
        assert row['taxable_amount'] == get_current_version(check_for_updates=False).our_version
    elif row['free_amount'] == 'taxfree_after_period':
        assert row['taxable_amount'] == str(accountant.pots[0].settings.taxfree_after_period)
    else:
        _check_boolean_settings(row, accountant)


def _check_column(attribute: str, index: int, sheet_id: str, expected, got_columns: List[List[str]]):  # noqa: E501
    expected_value = FVal(expected[attribute])
    got_value = FVal(got_columns[index][0])
    msg = f'Sheet: {sheet_id}, row: {index + CSV_INDEX_OFFSET} {attribute} mismatch. {got_value} != {expected_value}'  # noqa: E501
    assert expected_value.is_close(got_value), msg


def upload_csv_and_check(
        service: 'GoogleService',
        csv_data: List[Dict[str, Any]],
        expected_csv_data: List[Dict[str, Any]],
) -> None:
    """Creates a new google sheet, uploads the CSV and then checks it renders properly"""
    sheet_id = service.create_spreadsheet()
    service.add_rows(sheet_id=sheet_id, csv_data=csv_data)
    result = service.get_cell_ranges(
        sheet_id=sheet_id,
        range_names=['I2:I', 'J2:J'],
    )
    # Check that the data length matches
    assert len(result[0]['values']) == len(expected_csv_data)
    assert len(result[1]['values']) == len(expected_csv_data)
    for idx, expected in enumerate(expected_csv_data):
        _check_column(
            attribute='pnl_taxable',
            index=idx,
            sheet_id=sheet_id,
            expected=expected,
            got_columns=result[0]['values'],
        )
        _check_column(
            attribute='pnl_free',
            index=idx,
            sheet_id=sheet_id,
            expected=expected,
            got_columns=result[1]['values'],
        )


def assert_csv_export(
        accountant: 'Accountant',
        expected_pnls: PnlTotals,
        google_service: Optional['GoogleService'] = None,
) -> None:
    """Test the contents of the csv export match the actual result

    If google_service exists then it's also uploaded to a sheet to check the formular rendering
    """
    csvexporter = accountant.csvexporter
    if len(accountant.pots[0].processed_events) == 0:
        return  # nothing to do for no events as no csv is generated

    with tempfile.TemporaryDirectory() as tmpdirname:
        tmpdir = Path(tmpdirname)
        # first make sure we export without formulas
        csvexporter.settings = csvexporter.settings._replace(pnl_csv_with_formulas=False)
        accountant.csvexporter.export(
            events=accountant.pots[0].processed_events,
            pnls=accountant.pots[0].pnls,
            directory=tmpdir,
        )

        calculated_pnls = PnlTotals()
        expected_csv_data = []
        with open(tmpdir / FILENAME_ALL_CSV, newline='') as csvfile:
            reader = csv.DictReader(csvfile)
            for row in reader:
                expected_csv_data.append(row)
                if row['type'] == '':
                    continue  # have summaries and reached the end

                event_type = AccountingEventType.deserialize(row['type'])
                taxable = FVal(row['pnl_taxable'])
                free = FVal(row['pnl_free'])
                if taxable != ZERO or free != ZERO:
                    calculated_pnls[event_type] += PNL(taxable=taxable, free=free)

        assert_pnl_totals_close(expected_pnls, calculated_pnls)

        # export with formulas and summary
        csvexporter.settings = csvexporter.settings._replace(pnl_csv_with_formulas=True, pnl_csv_have_summary=True)  # noqa: E501
        accountant.csvexporter.export(
            events=accountant.pots[0].processed_events,
            pnls=accountant.pots[0].pnls,
            directory=tmpdir,
        )
        index = CSV_INDEX_OFFSET
        at_summaries = False
        to_upload_data = []
        with open(tmpdir / FILENAME_ALL_CSV, newline='') as csvfile:
            reader = csv.DictReader(csvfile)
            for row in reader:
                to_upload_data.append(row)

                if at_summaries:
                    _check_summaries_row(row, accountant)
                    continue

                if row['type'] == '':
                    at_summaries = True
                    continue  # have summaries and reached the end

                if row['pnl_taxable'] != '0':
                    value = f'G{index}*H{index}'
                    if row['type'] == AccountingEventType.TRADE and 'Amount out' in row['notes']:
                        assert row['pnl_taxable'] == f'={value}-J{index}'
                    elif row['type'] == AccountingEventType.FEE:
                        assert row['pnl_taxable'] == f'={value}+{value}-J{index}'

                if row['pnl_free'] != '0':
                    value = f'F{index}*H{index}'
                    if row['type'] == AccountingEventType.TRADE and 'Amount out' in row['notes']:
                        assert row['pnl_free'] == f'={value}-L{index}'
                    elif row['type'] == AccountingEventType.FEE:
                        assert row['pnl_free'] == f'={value}+{value}-:{index}'

                index += 1

        if google_service is not None:
            upload_csv_and_check(
                service=google_service,
                csv_data=to_upload_data,
                expected_csv_data=expected_csv_data,
            )
