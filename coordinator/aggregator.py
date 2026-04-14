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
STATE_PATH = SHARED_WEIGHTS_DIR / "coordinator_state.json"


def env(name: str, default: str | None = None, required: bool = False) -> str:
    value = os.getenv(name, default)
    if required and not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value or ""


def load_contract(web3: Web3):
    artifact = json.loads(Path(env("ABI_PATH", str(ABI_PATH))).read_text())
    contract_address = Web3.to_checksum_address(env("CONTRACT_ADDRESS", required=True))
    return web3.eth.contract(address=contract_address, abi=artifact["abi"])


def load_state() -> dict:
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text())
    return {"last_processed_block": -1, "processed_rounds": []}


def save_state(state: dict) -> None:
    SHARED_WEIGHTS_DIR.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2))


def build_tx(web3: Web3, account, nonce: int) -> dict:
    return {
        "from": account.address,
        "nonce": nonce,
        "chainId": web3.eth.chain_id,
        "gasPrice": web3.eth.gas_price,
    }


def send_transaction(web3: Web3, account, fn, nonce: int) -> int:
    tx = fn.build_transaction(build_tx(web3, account, nonce))
    signed = account.sign_transaction(tx)
    tx_hash = web3.eth.send_raw_transaction(signed.raw_transaction)
    receipt = web3.eth.wait_for_transaction_receipt(tx_hash)
    if receipt.status != 1:
        raise RuntimeError(f"Transaction failed: {tx_hash.hex()}")
    return nonce + 1


def fetch_round_updates(contract, round_id: int) -> list[dict]:
    updates = []
    count = contract.functions.getRoundUpdateCount(round_id).call()
    for index in range(count):
        client, model_hash = contract.functions.getRoundUpdate(round_id, index).call()
        updates.append({"client": client, "model_hash": model_hash})
    return updates


def aggregate_round(round_id: int, updates: list[dict]) -> dict:
    joined_hashes = "|".join(sorted(update["model_hash"] for update in updates))
    aggregate_hash = hashlib.sha256(joined_hashes.encode("utf-8")).hexdigest()
    result = {
        "round": round_id,
        "aggregate_hash": aggregate_hash,
        "num_updates": len(updates),
        "updates": updates,
    }
    SHARED_WEIGHTS_DIR.mkdir(parents=True, exist_ok=True)
    output_path = SHARED_WEIGHTS_DIR / f"round_{round_id}_aggregate.json"
    output_path.write_text(json.dumps(result, indent=2))
    return result


def reward_clients(web3: Web3, contract, account, nonce: int, updates: list[dict]) -> int:
    for update in updates:
        nonce = send_transaction(web3, account, contract.functions.reward(update["client"]), nonce)
    return nonce


def main() -> int:
    rpc_url = env("RPC_URL", "http://127.0.0.1:8545")
    poll_interval = float(env("POLL_INTERVAL", "2"))
    max_rounds = int(env("MAX_ROUNDS", "1"))
    private_key = env("COORDINATOR_PRIVATE_KEY", os.getenv("PRIVATE_KEY", ""))

    web3 = Web3(Web3.HTTPProvider(rpc_url))
    if not web3.is_connected():
        raise RuntimeError(f"Unable to connect to RPC at {rpc_url}")

    contract = load_contract(web3)
    state = load_state()
    processed_rounds = set(state.get("processed_rounds", []))
    nonce = None
    account = None

    if private_key:
        account = web3.eth.account.from_key(private_key)
        nonce = web3.eth.get_transaction_count(account.address)

    processed_count = 0
    while processed_count < max_rounds:
        latest_block = web3.eth.block_number
        from_block = max(0, state.get("last_processed_block", -1) + 1)

        if latest_block >= from_block:
            events = contract.events.RoundCompleted().get_logs(from_block=from_block, to_block=latest_block)
            for event in events:
                round_id = int(event["args"]["round"])
                if round_id in processed_rounds:
                    continue

                updates = fetch_round_updates(contract, round_id)
                result = aggregate_round(round_id, updates)
                print(f"aggregated round {round_id}: {result['aggregate_hash']}")

                if account is not None and nonce is not None:
                    nonce = reward_clients(web3, contract, account, nonce, updates)
                    print(f"rewarded {len(updates)} clients for round {round_id}")

                processed_rounds.add(round_id)
                processed_count += 1
                state["processed_rounds"] = sorted(processed_rounds)
                if processed_count >= max_rounds:
                    break

            state["last_processed_block"] = latest_block
            save_state(state)

        time.sleep(poll_interval)

    print(f"coordinator processed {processed_count} round(s)")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"aggregator error: {exc}", file=sys.stderr)
        raise
