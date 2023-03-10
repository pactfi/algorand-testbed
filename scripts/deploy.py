import copy
import os
import pathlib
import sys
from base64 import b64decode
from typing import Optional, Union

import algosdk
import click
import dotenv
from algosdk import abi, transaction
from algosdk.logic import get_application_address
from algosdk.v2client.algod import AlgodClient
from Cryptodome.Hash import SHA512

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


CONTRACTS_PATH = pathlib.Path(__file__).parent.parent / "contracts"

MICRO_FARM_APPROVAL_COMPILED_B64 = "ByADAAEGJgEJTWFzdGVyQXBwMgkxABJEMRlAAH0xGEAAMjYaAIAEpg4KhxJEKDYaARfAMmexJLIQNhoCF8AyshiABLc1X9GyGjIJsiAisgGzQgBiNhoAgAQNhhxCEkSxJLIQKGSyGIAE+mtcoLIaIxayGiIWshojFrIaIhayGjIJshwyCLIyNhoCF8AwsjAyCbIgIrIBs0IAHDEZgQUSQAABADIJKGRhFESxI7IQIrIBMgmyILMjQw=="


def get_selector(method_signature: str) -> bytes:
    hash_ = SHA512.new(truncate="256")
    hash_.update(method_signature.encode("utf-8"))
    return hash_.digest()[:4]


def get_contract_txn(
    contract_type: str,
    global_schema,
    teal_version: int,
    version: int,
    app_args: Optional[list] = None,
    **format_data,
):
    sender = DEPLOYER_ADDRESS
    if version > 0:
        contract_type += f"_v_{version}"

    path = CONTRACTS_PATH / f"{contract_type}.teal"
    with open(path, "r") as file_:
        ssc_teal = file_.read().format(**format_data)
    clear_teal = f"#pragma version {teal_version}\nint 1"

    # compile contracts to bytecode
    compiled_clear = client.compile(clear_teal)
    compiled_SSC = client.compile(ssc_teal)

    ssc_raw: str = compiled_SSC["result"]
    clear_raw: str = compiled_clear["result"]

    return transaction.ApplicationCreateTxn(
        sender=sender,
        sp=client.suggested_params(),
        on_complete=transaction.OnComplete.NoOpOC,
        approval_program=b64decode(ssc_raw),
        clear_program=b64decode(clear_raw),
        global_schema=global_schema,
        local_schema=transaction.StateSchema(0, 0),
        foreign_assets=[
            format_data["primary_asset_id"],
            format_data["secondary_asset_id"],
        ],
        extra_pages=1,
        app_args=app_args,
    )


@click.group()
def deploy_contract():
    print("Contract deployment begins")


@deploy_contract.command()
@click.option(
    "--contract-type",
    type=click.Choice(["constant_product", "stableswap", "nft_constant_product"]),
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
def exchange(
    contract_type: str,
    primary_asset_id: int,
    secondary_asset_id: int,
    fee_bps: int,
    version: Union[int, None],
    pact_fee_bps: int,
    amplifier: int,
    admin_and_treasury_address: str,
):
    contract_dict = {
        ("nft_constant_product", None): (transaction.StateSchema(9, 4), 6),
        ("constant_product", None): (transaction.StateSchema(9, 4), 8),
        ("constant_product", 201): (transaction.StateSchema(9, 4), 8),
        ("constant_product", 2): (transaction.StateSchema(9, 4), 6),
        ("constant_product", 1): (transaction.StateSchema(4, 1), 5),
        ("stableswap", 1): (transaction.StateSchema(13, 4), 6),
        ("stableswap", None): (transaction.StateSchema(13, 4), 6),
    }

    app_args: list = []
    if contract_type == "constant_product" and (version is None or version >= 200):
        # Old contracts have parameters hardcoded. New contracts are configured in a transaction creating the contract.
        app_args = [
            fee_bps,
            pact_fee_bps,
            abi.AddressType().encode(admin_and_treasury_address),
            abi.AddressType().encode(admin_and_treasury_address),
        ]

    global_schema, teal_version = contract_dict[(contract_type, version)]
    contract_txn = get_contract_txn(
        contract_type=contract_type,
        global_schema=global_schema,
        teal_version=teal_version,
        version=version or 0,
        app_args=app_args,
        primary_asset_id=primary_asset_id,
        secondary_asset_id=secondary_asset_id,
        fee_bps=fee_bps,
        pact_fee_bps=pact_fee_bps,
        initial_A=amplifier,
        admin_and_treasury_address=admin_and_treasury_address,
    )

    print("Deploying app...")
    contract_txid = client.send_transaction(contract_txn.sign(DEPLOYER_PK))
    tx_info = wait_for_confirmation(client, contract_txid)

    app_id = tx_info["application-index"]
    print("Deployed APP ID:", app_id)

    print("Funding contract account...")
    # Fund contract account
    # 100k account + 100k ASA optins + 100k
    amt = 400000
    if primary_asset_id == 0:
        amt = 300000
    send_and_wait(
        client,
        transaction.PaymentTxn(
            DEPLOYER_ADDRESS,
            client.suggested_params(),
            get_application_address(app_id),
            amt,
        ).sign(DEPLOYER_PK),
    )

    print("Creating liquidity tokens")
    sp_large = client.suggested_params()
    sp_large.flat_fee = True
    sp_large.fee = 2000
    client.send_transaction(
        transaction.ApplicationNoOpTxn(
            DEPLOYER_ADDRESS,
            sp_large,
            app_id,
            app_args=[ACTION.CREATE_LIQUIDITY_TOKEN],
            foreign_assets=[primary_asset_id, secondary_asset_id],
        ).sign(DEPLOYER_PK)
    )

    print("Opting-in contract to currency")
    sp_large = client.suggested_params()
    sp_large.flat_fee = True
    sp_large.fee = 3000  # send fee enabling opting in maximally two assets
    txid = client.send_transaction(
        transaction.ApplicationNoOpTxn(
            DEPLOYER_ADDRESS,
            sp_large,
            app_id,
            app_args=[ACTION.OPT_IN_TO_ASSETS],
            foreign_assets=[primary_asset_id, secondary_asset_id],
        ).sign(DEPLOYER_PK)
    )
    wait_for_confirmation(client, txid)


@deploy_contract.command()
def gas_station():
    path = pathlib.Path(__file__).parent.parent / "contracts" / "gas_station.teal"
    with open(path, "r") as file_:
        ssc_teal = file_.read()
    clear_teal = "#pragma version 7\npushint 0 // 0\nreturn"

    compiled_clear = client.compile(clear_teal)
    compiled_SSC = client.compile(ssc_teal)

    ssc_raw: str = compiled_SSC["result"]
    clear_raw: str = compiled_clear["result"]

    create_app_tx = transaction.ApplicationCreateTxn(
        sender=DEPLOYER_ADDRESS,
        on_complete=transaction.OnComplete.NoOpOC,
        approval_program=b64decode(ssc_raw),
        clear_program=b64decode(clear_raw),
        global_schema=transaction.StateSchema(0, 0),
        local_schema=transaction.StateSchema(0, 0),
        sp=client.suggested_params(),
    )

    txid = client.send_transaction(create_app_tx.sign(DEPLOYER_PK))
    res = wait_for_confirmation(client, txid)
    app_id = res["application-index"]

    print("Deployed APP ID:", app_id)


@deploy_contract.command()
@click.option(
    "--staked-asset-id",
    type=int,
    prompt="Staked asset id",
)
@click.option(
    "--gas-station-id",
    type=int,
    prompt="Gas station app id",
)
@click.option(
    "--admin",
    type=str,
    prompt="Admin address",
)
def farm(staked_asset_id: int, gas_station_id: int, admin: str):
    path_approval = CONTRACTS_PATH / "farm.teal"
    path_clear = CONTRACTS_PATH / "farm_clear.teal"
    with open(path_approval, "r") as file_:
        approval_teal = file_.read()
        # Make the contract operate on blocks instead of seconds. Makes the testing easier by avoiding the need to wait some to time before the rewards accrue.
        approval_teal = approval_teal.replace("LatestTimestamp", "Round")

    with open(path_clear, "r") as file_:
        clear_teal = file_.read()

    # compile contracts to bytecode
    compiled_approval = b64decode(client.compile(approval_teal)["result"])
    compiled_clear = b64decode(client.compile(clear_teal)["result"])

    suggested_params = client.suggested_params()

    ESCROW_LEN = 346
    BOX_COST = 2500 + 400 * (ESCROW_LEN + len("Escrow"))
    fund_tx = transaction.PaymentTxn(
        sender=DEPLOYER_ADDRESS,
        receiver=algosdk.logic.get_application_address(gas_station_id),
        amt=100_000 + BOX_COST,
        sp=suggested_params,
    )

    create_tx = transaction.ApplicationCreateTxn(
        sender=DEPLOYER_ADDRESS,
        sp=sp_fee(suggested_params, 3000),
        on_complete=transaction.OnComplete.NoOpOC,
        approval_program=compiled_approval,
        clear_program=compiled_clear,
        global_schema=transaction.StateSchema(7, 10),
        local_schema=transaction.StateSchema(2, 4),
        foreign_assets=[staked_asset_id],
        foreign_apps=[gas_station_id],
        accounts=[admin],
        boxes=[(0, "Escrow")],
        app_args=[
            get_selector("create(application,asset,account,account)void"),
            abi.UintType(8).encode(1),
            0,
            0,
            0,
        ],
        extra_pages=2,
    )

    txs = transaction.assign_group_id([fund_tx, create_tx])
    client.send_transactions(tx.sign(DEPLOYER_PK) for tx in txs)

    res = wait_for_confirmation(client, txs[-1].get_txid())
    app_id = res["application-index"]

    print("Deployed APP ID:", app_id)


@deploy_contract.command()
@click.option(
    "--contract-type",
    type=click.Choice(["constant_product", "stableswap"]),
    prompt="Contract type",
)
@click.option(
    "--admin-and-treasury-address",
    type=str,
    default="",
    prompt="Admin and treasury address",
)
def deploy_factory(contract_type: str, admin_and_treasury_address: str):
    path = CONTRACTS_PATH / f"factory_{contract_type}.teal"
    with open(path, "r") as file_:
        approval_teal = file_.read()
    clear_teal = "#pragma version 8\npushint 0 // 0\nreturn"

    # compile contracts to bytecode
    compiled_approval = b64decode(client.compile(approval_teal)["result"])
    compiled_clear = b64decode(client.compile(clear_teal)["result"])

    suggested_params = client.suggested_params()

    app_args = [
        get_selector("create(account,account)void"),
        0,
        1,
    ]

    create_tx = transaction.ApplicationCreateTxn(
        sender=DEPLOYER_ADDRESS,
        sp=sp_fee(suggested_params, 3000),
        on_complete=transaction.OnComplete.NoOpOC,
        approval_program=compiled_approval,
        clear_program=compiled_clear,
        global_schema=transaction.StateSchema(1, 4),
        local_schema=transaction.StateSchema(0, 0),
        extra_pages=1,
        app_args=app_args,
        accounts=[admin_and_treasury_address, admin_and_treasury_address],
    )

    client.send_transaction(create_tx.sign(DEPLOYER_PK))

    res = wait_for_confirmation(client, create_tx.get_txid())
    app_id = res["application-index"]

    # Fund factory's min balance.
    fund_tx = transaction.PaymentTxn(
        sender=DEPLOYER_ADDRESS,
        receiver=algosdk.logic.get_application_address(app_id),
        amt=100_000,
        sp=suggested_params,
    )
    client.send_transaction(fund_tx.sign(DEPLOYER_PK))
    res = wait_for_confirmation(client, fund_tx.get_txid())

    print("Deployed APP ID:", app_id)


def sp_fee(sp: transaction.SuggestedParams, fee: int) -> transaction.SuggestedParams:
    sp = copy.copy(sp)
    sp.flat_fee = True
    sp.fee = fee
    return sp


if __name__ == "__main__":
    deploy_contract()
