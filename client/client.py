import hashlib
import json
import os
import sys
import time
from pathlib import Path

import torch
import torch.nn as nn
import torch.optim as optim
from torch.utils.data import DataLoader
from web3 import Web3

ROOT = Path(__file__).resolve().parents[1]
ABI_PATH = ROOT / "blockchain" / "out" / "FLRegistry.sol" / "FLRegistry.json"
SHARED_WEIGHTS_DIR = ROOT / "shared_weights"
DATA_DIR = ROOT / "data"

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

def train(model, train_loader, optimizer, epochs=1):
    model.train()
    for epoch in range(epochs):
        for batch_idx, (data, target) in enumerate(train_loader):
            optimizer.zero_grad()
            output = model(data)
            loss = nn.functional.nll_loss(output, target)
            loss.backward()
            optimizer.step()
            if batch_idx % 100 == 0:
                print(f"Train Epoch: {epoch} [{batch_idx * len(data)}/{len(train_loader.dataset)}] Loss: {loss.item():.6f}")

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

def build_tx(web3: Web3, account, nonce: int) -> dict:
    return {
        "from": account.address,
        "nonce": nonce,
        "chainId": web3.eth.chain_id,
        "gasPrice": web3.eth.gas_price,
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

def perform_training_round(client_name: str, round_id: int) -> str:
    print(f"[{client_name}] Starting training for round {round_id}...")
    
    # 1. Load local data
    data_path = DATA_DIR / client_name / "train_data.pt"
    if not data_path.exists():
        raise FileNotFoundError(f"Data not found at {data_path}. Run data preparation first.")
    
    local_data = torch.load(data_path)
    train_loader = DataLoader(local_data, batch_size=64, shuffle=True)
    
    # 2. Setup model
    model = SimpleCNN()
    
    # 3. Load global model if it exists (for subsequent rounds)
    global_model_path = SHARED_WEIGHTS_DIR / "global_model.pth"
    if global_model_path.exists():
        print(f"[{client_name}] Loading global model from {global_model_path}")
        model.load_state_dict(torch.load(global_model_path))
    
    # 4. Train
    optimizer = optim.Adam(model.parameters(), lr=0.001)
    train(model, train_loader, optimizer, epochs=1)
    
    # 5. Save local weights
    SHARED_WEIGHTS_DIR.mkdir(parents=True, exist_ok=True)
    weights_filename = f"{client_name}_round_{round_id}.pth"
    weights_path = SHARED_WEIGHTS_DIR / weights_filename
    torch.save(model.state_dict(), weights_path)
    
    # 6. Generate hash of weights
    with open(weights_path, "rb") as f:
        weights_bytes = f.read()
        model_hash = hashlib.sha256(weights_bytes).hexdigest()
    
    # Save a metadata JSON for easier tracking (simulating IPFS metadata)
    metadata = {
        "client": client_name,
        "round": round_id,
        "weights_file": weights_filename,
        "hash": model_hash,
        "timestamp": int(time.time())
    }
    metadata_path = SHARED_WEIGHTS_DIR / f"{client_name}_round_{round_id}.json"
    metadata_path.write_text(json.dumps(metadata, indent=2))
    
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

        # actual training
        model_hash = perform_training_round(client_name, round_id)
        
        print(f"[{client_name}] submitting hash for round {round_id}: {model_hash}")
        receipt = send_transaction(web3, account, contract.functions.submitUpdate(model_hash), nonce)
        nonce += 1
        print(f"[{client_name}] submitted in tx {receipt.transactionHash.hex()}")
        
        # Wait for the next round (triggered by aggregator)
        wait_for_round_advance(contract, round_id, poll_interval)

    print(f"[{client_name}] completed {max_rounds} round(s)")
    return 0

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"client error: {exc}", file=sys.stderr)
        raise
