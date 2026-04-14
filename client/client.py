import hashlib
import json
import os
import sys
import time
from pathlib import Path

from web3 import Web3

ROOT = Path(__file__).resolve().parents[1]
ABI_PATH = ROOT / "blockchain" / "out" / "FLRegistry.sol" / "FLRegistry.json"
SHARED_WEIGHTS_DIR = ROOT / "shared_weights"


def env(name: str, default: str | None = None, required: bool = False) -> str:
    value = os.getenv(name, default)
    if required and not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value or ""


def load_contract(web3: Web3):
    artifact = json.loads(Path(env("ABI_PATH", str(ABI_PATH))).read_text())
    contract_address = Web3.to_checksum_address(env("CONTRACT_ADDRESS", required=True))
    return web3.eth.contract(address=contract_address, abi=artifact["abi"])


def build_tx(web3: Web3, account, nonce: int) -> dict:
    chain_id = web3.eth.chain_id
    gas_price = web3.eth.gas_price
    return {
        "from": account.address,
        "nonce": nonce,
        "chainId": chain_id,
        "gasPrice": gas_price,
    }


def send_transaction(web3: Web3, account, fn, nonce: int):
    tx = fn.build_transaction(build_tx(web3, account, nonce))
    signed = account.sign_transaction(tx)
    tx_hash = web3.eth.send_raw_transaction(signed.raw_transaction)
    receipt = web3.eth.wait_for_transaction_receipt(tx_hash)
    if receipt.status != 1:
        raise RuntimeError(f"Transaction failed: {tx_hash.hex()}")
    return receipt


def ensure_registered(web3: Web3, contract, account, nonce: int) -> int:
    if contract.functions.registeredClients(account.address).call():
        print(f"[{account.address}] already registered")
        return nonce

    receipt = send_transaction(web3, account, contract.functions.registerClient(), nonce)
    print(f"[{account.address}] registered in tx {receipt.transactionHash.hex()}")
    return nonce + 1


def generate_model_hash(client_name: str, round_id: int) -> str:
    payload = {
        "client": client_name,
        "round": round_id,
        "seed": env("TRAINING_SEED", "blockfed-demo"),
        "timestamp": int(time.time()),
    }
    payload_bytes = json.dumps(payload, sort_keys=True).encode("utf-8")
    model_hash = hashlib.sha256(payload_bytes).hexdigest()

    SHARED_WEIGHTS_DIR.mkdir(parents=True, exist_ok=True)
    local_record = SHARED_WEIGHTS_DIR / f"{client_name}_round_{round_id}.json"
    local_record.write_text(json.dumps({"model_hash": model_hash, "payload": payload}, indent=2))
    return model_hash


def wait_for_round_advance(contract, previous_round: int, poll_interval: float) -> int:
    while True:
        current_round = contract.functions.currentRound().call()
        if current_round > previous_round:
            return current_round
        time.sleep(poll_interval)


def main() -> int:
    rpc_url = env("RPC_URL", "http://127.0.0.1:8545")
    private_key = env("PRIVATE_KEY", required=True)
    client_name = env("CLIENT_NAME", "client")
    max_rounds = int(env("MAX_ROUNDS", "1"))
    poll_interval = float(env("POLL_INTERVAL", "2"))

    web3 = Web3(Web3.HTTPProvider(rpc_url))
    if not web3.is_connected():
        raise RuntimeError(f"Unable to connect to RPC at {rpc_url}")

    account = web3.eth.account.from_key(private_key)
    contract = load_contract(web3)
    nonce = web3.eth.get_transaction_count(account.address)

    nonce = ensure_registered(web3, contract, account, nonce)

    for _ in range(max_rounds):
        round_id = contract.functions.currentRound().call()
        if contract.functions.submitted(round_id, account.address).call():
            print(f"[{client_name}] already submitted for round {round_id}, waiting for next round")
            round_id = wait_for_round_advance(contract, round_id, poll_interval)

        model_hash = generate_model_hash(client_name, round_id)
        print(f"[{client_name}] submitting hash for round {round_id}: {model_hash}")
        receipt = send_transaction(web3, account, contract.functions.submitUpdate(model_hash), nonce)
        nonce += 1
        print(f"[{client_name}] submitted in tx {receipt.transactionHash.hex()}")
        wait_for_round_advance(contract, round_id, poll_interval)

    print(f"[{client_name}] completed {max_rounds} round(s)")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"client error: {exc}", file=sys.stderr)
        raise
