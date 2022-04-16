import requests
from eth_utils import to_checksum_address

from rotkehlchen.tests.utils.api import api_url_for, assert_proper_response


def test_reverse_ens(rotkehlchen_api_server):
    """Test that we can reverse resolve ENS names"""
    addrs_1 = [
        to_checksum_address('0x9531c059098e3d194ff87febb587ab07b30b1306'),
        to_checksum_address('0x2b888954421b424c5d3d9ce9bb67c9bd47537d12'),
    ]
    response = requests.get(
        api_url_for(
            rotkehlchen_api_server,
            "reverseensresource",
            ethereum_addresses=addrs_1,
        ),
    )
    assert_proper_response(response)
    result = response.json()['result']
    expected_resp_1 = {
        addrs_1[0]: 'rotki.eth',
        addrs_1[1]: 'lefteris.eth',
    }
    assert result == expected_resp_1
    assert rotkehlchen_api_server.rest_api.rotkehlchen.data.db.get_reverse_ens(addrs_1) == expected_resp_1  # noqa: E501

    addrs_2 = [
        to_checksum_address('0x9531c059098e3d194ff87febb587ab07b30b1306'),
        to_checksum_address('0xa4b73b39f73f73655e9fdc5d167c21b3fa4a1ed6'),
        to_checksum_address('0x71C7656EC7ab88b098defB751B7401B5f6d8976F'),
    ]
    response = requests.patch(
        api_url_for(
            rotkehlchen_api_server,
            "reverseensresource",
            ethereum_addresses=addrs_2,
        ),
    )
    assert_proper_response(response)
    result = response.json()['result']
    expected_resp_2 = {
        addrs_2[0]: 'rotki.eth',
        addrs_2[1]: 'abc.eth',
        addrs_2[2]: None,
    }
    expected_db_result = expected_resp_1.copy()
    expected_db_result.update(expected_resp_2)
    assert result == expected_resp_2
    assert rotkehlchen_api_server.rest_api.rotkehlchen.data.db.get_reverse_ens(list(set(addrs_1) | set(addrs_2))) == expected_db_result  # noqa: E501

    response = requests.patch(
        api_url_for(
            rotkehlchen_api_server,
            "reverseensresource",
            ethereum_addresses=['0xqwerty'],
        ),
    )

    assert response.status_code == 400
