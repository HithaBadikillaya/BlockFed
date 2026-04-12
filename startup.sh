#!/bin/bash

# 1. Start Anvil in the background
echo "Starting Anvil (Local Blockchain)..."
anvil > anvil.log 2>&1 &
ANVIL_PID=$!

# Wait for Anvil to wake up
sleep 3

# 2. Deploy the Smart Contract
echo "📜 Deploying Smart Contract..."
cd blockchain
# Capture the output to extract the contract address
DEPLOY_LOG=$(forge create --rpc-url http://127.0.0.1:8545 --private-key 0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80 src/FLRegistry.sol:FLRegistry)
echo "$DEPLOY_LOG"

# Extract address (this regex looks for the 0x... address in the Forge output)
CONTRACT_ADDRESS=$(echo "$DEPLOY_LOG" | grep "Deployed to:" | awk '{print $3}')
echo "✅ Contract Deployed at: $CONTRACT_ADDRESS"

# 3. Update Environment Files automatically
echo "⚙️ Updating .env files..."
cd ..
echo "NEXT_PUBLIC_CONTRACT_ADDRESS=$CONTRACT_ADDRESS" > dashboard/.env.local
echo "CONTRACT_ADDRESS=$CONTRACT_ADDRESS" > .env

# 4. Start the Frontend and Aggregator
echo "🖥️ Starting Dashboard & Aggregator..."
cd dashboard && npm run dev &
cd ../coordinator && python aggregator.py &

echo "All systems GO! Press Ctrl+C to stop everything."

# Cleanup on exit
trap "kill $ANVIL_PID; exit" INT
wait