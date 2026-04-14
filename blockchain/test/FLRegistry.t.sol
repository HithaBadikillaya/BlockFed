// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import {FLRegistry} from "../src/FLRegistry.sol";

interface Vm {
    function prank(address msgSender) external;
    function expectRevert(bytes calldata) external;
}

address constant HEVM_ADDRESS = address(uint160(uint256(keccak256("hevm cheat code"))));
Vm constant VM = Vm(HEVM_ADDRESS);

contract FLRegistryTest {
    FLRegistry private registry;

    address private constant CLIENT_ONE = address(0x1);
    address private constant CLIENT_TWO = address(0x2);
    address private constant CLIENT_THREE = address(0x3);

    function setUp() public {
        registry = new FLRegistry();
    }

    function testClientCanRegister() public {
        VM.prank(CLIENT_ONE);
        registry.registerClient();

        require(registry.registeredClients(CLIENT_ONE), "client should be registered");
    }

    function testUnregisteredClientCannotSubmit() public {
        VM.prank(CLIENT_ONE);
        VM.expectRevert(bytes("Not registered"));
        registry.submitUpdate("hash-a");
    }

    function testRoundCompletesAfterRequiredSubmissions() public {
        _registerClient(CLIENT_ONE);
        _registerClient(CLIENT_TWO);
        _registerClient(CLIENT_THREE);

        VM.prank(CLIENT_ONE);
        registry.submitUpdate("hash-a");

        VM.prank(CLIENT_TWO);
        registry.submitUpdate("hash-b");

        VM.prank(CLIENT_THREE);
        registry.submitUpdate("hash-c");

        require(registry.currentRound() == 1, "round should advance");
        require(registry.getRoundUpdateCount(0) == 3, "round should store three updates");

        (address client, string memory modelHash) = registry.getRoundUpdate(0, 1);
        require(client == CLIENT_TWO, "second client should be stored");
        require(
            keccak256(bytes(modelHash)) == keccak256(bytes("hash-b")),
            "second hash should be stored"
        );
    }

    function testClientCannotSubmitTwiceInSameRound() public {
        _registerClient(CLIENT_ONE);

        VM.prank(CLIENT_ONE);
        registry.submitUpdate("hash-a");

        VM.prank(CLIENT_ONE);
        VM.expectRevert(bytes("Already submitted"));
        registry.submitUpdate("hash-b");
    }

    function _registerClient(address client) private {
        VM.prank(client);
        registry.registerClient();
    }
}
