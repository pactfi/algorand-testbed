import os
import sys
from typing import List, Union

from algosdk.transaction import (
    SignedTransaction,
    SuggestedParams,
)
from algosdk.v2client.algod import AlgodClient

# add "../" to path
curr_dir = os.path.join(os.path.dirname(__file__))
contracts_path = os.path.abspath(os.path.join(curr_dir, "../"))
sys.path.append(contracts_path)


def get_suggested_params(client: AlgodClient) -> SuggestedParams:
    return client.suggested_params()


# Function from Algorand Inc.
def wait_for_confirmation(client: AlgodClient, txid: str) -> dict:
    """
    Utility function to wait until the transaction is
    confirmed before proceeding.
    """
    last_round = client.status().get("last-round")
    txinfo = client.pending_transaction_info(txid)
    while not (txinfo.get("confirmed-round") and txinfo.get("confirmed-round") > 0):
        print("Waiting for confirmation")
        last_round += 1
        client.status_after_block(last_round)
        txinfo = client.pending_transaction_info(txid)
    print(
        "Transaction {} confirmed in round {}.".format(
            txid, txinfo.get("confirmed-round")
        )
    )
    return txinfo


def send_and_wait(
    client: AlgodClient, txn: Union[SignedTransaction, List[SignedTransaction]]
) -> dict:
    if isinstance(txn, list):
        txid = client.send_transactions(txn)
    else:
        txid = client.send_transaction(txn)
    return wait_for_confirmation(client, txid)
