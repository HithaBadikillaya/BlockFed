// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

contract FLRegistry {
    struct Update {
        address client;
        string modelHash;
    }

    uint256 public currentRound;
    uint256 public requiredClients = 3;

    mapping(uint256 => Update[]) private roundUpdates;
    mapping(address => bool) public registeredClients;
    mapping(uint256 => mapping(address => bool)) public submitted;
    mapping(address => uint256) public reputation;

    event ClientRegistered(address client);
    event UpdateSubmitted(address client, uint256 round, string hash);
    event RoundCompleted(uint256 round);

    modifier onlyRegistered() {
        _requireRegistered();
        _;
    }

    function registerClient() external {
        require(!registeredClients[msg.sender], "Already registered");
        registeredClients[msg.sender] = true;
        emit ClientRegistered(msg.sender);
    }

    function submitUpdate(string memory modelHash) external onlyRegistered {
        require(!submitted[currentRound][msg.sender], "Already submitted");

        uint256 round = currentRound;
        submitted[round][msg.sender] = true;
        roundUpdates[round].push(Update({client: msg.sender, modelHash: modelHash}));

        emit UpdateSubmitted(msg.sender, round, modelHash);

        if (roundUpdates[round].length >= requiredClients) {
            emit RoundCompleted(round);
            currentRound = round + 1;
        }
    }

    function reward(address client) external {
        reputation[client] += 10;
    }

    function penalize(address client) external {
        reputation[client] -= 5;
    }

    function getRoundUpdateCount(uint256 round) external view returns (uint256) {
        return roundUpdates[round].length;
    }

    function getRoundUpdate(uint256 round, uint256 index) external view returns (address client, string memory modelHash) {
        Update storage update = roundUpdates[round][index];
        return (update.client, update.modelHash);
    }

    function _requireRegistered() internal view {
        require(registeredClients[msg.sender], "Not registered");
    }
}
