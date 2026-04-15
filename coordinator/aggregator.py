import hashlib
import json
import os
import sys
import time
from pathlib import Path
from collections import OrderedDict

import torch
import torch.nn as nn
from web3 import Web3

ROOT = Path(__file__).resolve().parents[1]
ABI_PATH = ROOT / "blockchain" / "out" / "FLRegistry.sol" / "FLRegistry.json"
SHARED_WEIGHTS_DIR = ROOT / "shared_weights"
STATE_PATH = SHARED_WEIGHTS_DIR / "coordinator_state.json"

# Machine Learning Components 

class SimpleCNN(nn.Module):
    def __init__(self):
        super(SimpleCNN, self).__init__()
        self.conv1 = nn.Conv2d(1, 32, 3, 1)
        self.conv2 = nn.Conv2d(32, 64, 3, 1)
        self.dropout1 = nn.Dropout(0.25)
        self.dropout2 = nn.Dropout(0.5)
        self.fc1 = nn.Linear(9216, 128)
        self.fc2 = nn.Linear(128, 10)

    def forward(self, x):
        x = self.conv1(x)
        x = torch.relu(x)
        x = self.conv2(x)
        x = torch.relu(x)
        x = torch.max_pool2d(x, 2)
        x = self.dropout1(x)
        x = torch.flatten(x, 1)
        x = self.fc1(x)
        x = torch.relu(x)
        x = self.dropout2(x)
        x = self.fc2(x)
        return torch.log_softmax(x, dim=1)

def federated_averaging(local_weights_paths):
    """
    Implements the FedAvg algorithm: Average weights of all participating clients.
    """
    print(f"Aggregating {len(local_weights_paths)} models using FedAvg...")
    
    global_state_dict = None
    
    for path in local_weights_paths:
        local_state_dict = torch.load(path)
        
        if global_state_dict is None:
            global_state_dict = OrderedDict()
            for key in local_state_dict.keys():
                global_state_dict[key] = local_state_dict[key].clone()
        else:
            for key in local_state_dict.keys():
                global_state_dict[key] += local_state_dict[key]
                
    # Average the weights
    for key in global_state_dict.keys():
        global_state_dict[key] = global_state_dict[key] / len(local_weights_paths)
        
    return global_state_dict

# Blockchain & Infrastructure Components

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

def aggregate_round(round_id: int, updates: list[dict]) -> dict:
    print(f"=== Aggregating Round {round_id} ===")
    
    local_weights_paths = []
    
    for update in updates:
        client_name = f"client{updates.index(update) + 1}" 
        weights_file = None
        for file in SHARED_WEIGHTS_DIR.glob(f"*_round_{round_id}.json"):
            meta = json.loads(file.read_text())
            if meta["hash"] == update["model_hash"]:
                weights_file = SHARED_WEIGHTS_DIR / meta["weights_file"]
                break
        
        if weights_file and weights_file.exists():
            local_weights_paths.append(weights_file)
        else:
            print(f"Warning: Could not find weights for hash {update['model_hash']}")

    if not local_weights_paths:
        raise RuntimeError(f"No weight files found for round {round_id}")

    # 1. Perform FedAvg
    global_weights = federated_averaging(local_weights_paths)
    
    # 2. Save global model
    global_model_path = SHARED_WEIGHTS_DIR / "global_model.pth"
    torch.save(global_weights, global_model_path)
    
    # 3. Generate global hash
    with open(global_model_path, "rb") as f:
        global_hash = hashlib.sha256(f.read()).hexdigest()

    result = {
        "round": round_id,
        "global_hash": global_hash,
        "num_updates": len(updates),
        "clients": [u["client"] for u in updates]
    }
    
    summary_path = SHARED_WEIGHTS_DIR / f"round_{round_id}_summary.json"
    summary_path.write_text(json.dumps(result, indent=2))
    
    print(f"Successfully aggregated round {round_id}. Global Hash: {global_hash}")
    return result

def reward_clients(web3: Web3, contract, account, nonce: int, updates: list[dict]) -> int:
    for update in updates:
        # Simple reward logic
        print(f"Rewarding client {update['client']}...")
        tx = contract.functions.reward(update["client"]).build_transaction({
            "from": account.address,
            "nonce": nonce,
            "chainId": web3.eth.chain_id,
            "gasPrice": web3.eth.gas_price,
        })
        signed = account.sign_transaction(tx)
        web3.eth.send_raw_transaction(signed.raw_transaction)
        nonce += 1
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
    account = web3.eth.account.from_key(private_key) if private_key else None
    
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

                # 1. Fetch all updates for this round
                updates = []
                count = contract.functions.getRoundUpdateCount(round_id).call()
                for i in range(count):
                    client, model_hash = contract.functions.getRoundUpdate(round_id, i).call()
                    updates.append({"client": client, "model_hash": model_hash})

                # 2. Aggregate
                try:
                    aggregate_round(round_id, updates)
                    
                    # 3. Reward (if we have a key)
                    if account:
                        nonce = web3.eth.get_transaction_count(account.address)
                        reward_clients(web3, contract, account, nonce, updates)
                except Exception as e:
                    print(f"Aggregation failed for round {round_id}: {e}")
                    continue

                processed_rounds.add(round_id)
                processed_count += 1
                state["processed_rounds"] = sorted(processed_rounds)
                if processed_count >= max_rounds:
                    break

            state["last_processed_block"] = latest_block
            save_state(state)

        time.sleep(poll_interval)

    print(f"Coordinator processed {processed_count} round(s)")
    return 0

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"aggregator error: {exc}", file=sys.stderr)
        raise
