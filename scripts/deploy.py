import os
import pathlib
import sys
from base64 import b64decode
from typing import Union

import algosdk
import click
import dotenv
from algosdk.future.transaction import (
    ApplicationCreateTxn,
    ApplicationNoOpTxn,
    OnComplete,
    PaymentTxn,
    StateSchema,
)
from algosdk.logic import get_application_address
from algosdk.v2client.algod import AlgodClient

sys.path.append(os.path.join(os.path.dirname(__file__), "../"))
from scripts.common import send_and_wait, wait_for_confirmation

dotenv.load_dotenv()
ALGOD_URL = os.getenv("ALGOD_URL")
ALGOD_TOKEN = os.getenv("ALGOD_TOKEN", "")
X_API_KEY = os.getenv("X_API_KEY", "")
DEPLOYER_MNEMONIC = os.getenv("DEPLOYER_MNEMONIC")
DEPLOYER_PK = algosdk.mnemonic.to_private_key(DEPLOYER_MNEMONIC)
DEPLOYER_ADDRESS = algosdk.account.address_from_private_key(DEPLOYER_PK)

client = AlgodClient(ALGOD_TOKEN, ALGOD_URL, headers={"x-api-key": X_API_KEY})


class ACTION:
    SWAP = "SWAP"
    ADD_LIQUIDITY = "ADDLIQ"
    REMOVE_LIQUIDITY = "REMLIQ"
    CREATE_LIQUIDITY_TOKEN = "CLT"
    OPT_IN_TO_ASSETS = "OPTIN"  # contract opt-in to assets


def get_contract_txn(
    contract_type: str, global_schema, teal_version: int, version: int, **format_data
):
    sender = DEPLOYER_ADDRESS
    contract_type = (
        contract_type + "_v_1"
        if (version == 1 and contract_type == "constant_product")
        else contract_type
    )
    path = pathlib.Path(__file__).parent.parent / f"{contract_type}.teal"
    with open(path, "r") as file_:
        ssc_teal = file_.read().format(**format_data)
    clear_teal = f"#pragma version {teal_version}\nint 1"
    # compile contracts to bytecode
    compiled_clear = client.compile(clear_teal)
    compiled_SSC = client.compile(ssc_teal)

    ssc_raw: str = compiled_SSC["result"]
    clear_raw: str = compiled_clear["result"]

    return ApplicationCreateTxn(
        sender,
        client.suggested_params(),
        OnComplete.NoOpOC,
        b64decode(ssc_raw),
        b64decode(clear_raw),
        global_schema,
        StateSchema(0, 0),
        foreign_assets=[
            format_data["primary_asset_id"],
            format_data["secondary_asset_id"],
        ],
        extra_pages=1,
    )


@click.command()
@click.option(
    "--contract-type",
    type=click.Choice(["constant_product", "stableswap"]),
    prompt="Contract type",
)
@click.option(
    "--primary_asset_id",
    default=0,
    type=int,
    prompt="Primary asset id",
    help="ID of the primary asset to be used on this exchange (must be 0 for Algos exchange).",
)
@click.option(
    "--secondary_asset_id",
    type=int,
    prompt="Secondary asset id",
    help="ID of the secondary exchanged asset.",
)
@click.option(
    "--fee_bps",
    type=int,
    prompt="Fee bps",
    help="Fee in basis points taken from the outcome of each swap.",
)
@click.option(
    "--pact_fee_bps",
    type=int,
    default=30,
    prompt="Pact Fee bps",
    help="Pact fee in basis points taken from the outcome of each swap. (stableswap only)",
)
@click.option(
    "--amplifier",
    type=int,
    default=1000,
    prompt="Amplifier",
    help="Stableswap amplifier. (stableswap only)",
)
@click.option(
    "--admin_and_treasury_address",
    type=str,
    default="",
    prompt="Admin and treasury address",
    help="Stableswap admin and treasury address. (stableswap only)",
)
@click.option(
    "--version",
    type=int,
    default=None,
    prompt=False,
    help="Smart contract version",
)
def deploy_contract(
    contract_type: str,
    primary_asset_id: int,
    secondary_asset_id: int,
    fee_bps: int,
    version: Union[int, None],
    pact_fee_bps: int,
    amplifier: int,
    admin_and_treasury_address: str,
):

    print("EC deployment begins")

    contract_dict = {
        ("constant_product", None): (StateSchema(9, 4), 6),
        ("constant_product", 2): (StateSchema(9, 4), 6),
        ("constant_product", 1): (StateSchema(4, 1), 5),
        ("stableswap", 1): (StateSchema(13, 4), 6),
        ("stableswap", None): (StateSchema(13, 4), 6),
    }

    global_schema, teal_version = contract_dict[(contract_type, version)]
    contract_txn = get_contract_txn(
        contract_type=contract_type,
        global_schema=global_schema,
        teal_version=teal_version,
        version=version,
        primary_asset_id=primary_asset_id,
        secondary_asset_id=secondary_asset_id,
        fee_bps=fee_bps,
        pact_fee_bps=pact_fee_bps,
        initial_A=amplifier,
        admin_and_treasury_address=admin_and_treasury_address,
    )

    print("Deploying EC...")
    contract_txid = client.send_transaction(contract_txn.sign(DEPLOYER_PK))
    tx_info = wait_for_confirmation(client, contract_txid)

    ec_id = tx_info["application-index"]
    print("Deployed EC ID:", ec_id)

    print("Funding contract account...")
    # Fund contract account
    # 100k account + 100k ASA optins + 100k
    amt = 400000
    if primary_asset_id == 0:
        amt = 300000
    send_and_wait(
        client,
        PaymentTxn(
            DEPLOYER_ADDRESS,
            client.suggested_params(),
            get_application_address(ec_id),
            amt,
        ).sign(DEPLOYER_PK),
    )

    print("Creating liquidity tokens")
    sp_large = client.suggested_params()
    sp_large.flat_fee = True
    sp_large.fee = 2000
    client.send_transaction(
        ApplicationNoOpTxn(
            DEPLOYER_ADDRESS,
            sp_large,
            ec_id,
            app_args=[ACTION.CREATE_LIQUIDITY_TOKEN],
            foreign_assets=[primary_asset_id, secondary_asset_id],
        ).sign(DEPLOYER_PK)
    )

    print("Opting-in contract to currency")
    sp_large = client.suggested_params()
    sp_large.flat_fee = True
    sp_large.fee = 3000  # send fee enabling opting in maximally two assets
    txid = client.send_transaction(
        ApplicationNoOpTxn(
            DEPLOYER_ADDRESS,
            sp_large,
            ec_id,
            app_args=[ACTION.OPT_IN_TO_ASSETS],
            foreign_assets=[primary_asset_id, secondary_asset_id],
        ).sign(DEPLOYER_PK)
    )
    wait_for_confirmation(client, txid)


if __name__ == "__main__":
    deploy_contract()
