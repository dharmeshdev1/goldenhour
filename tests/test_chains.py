"""Normalizers against response shapes recorded from the real APIs."""

from goldenhour.chains import bitcoin, etherscan, solana, thorchain, tron, xrpl
from goldenhour.models import Chain, Kind


def test_etherscan_normal_transaction_records_value_and_fee_for_sender() -> None:
    row = {
        "hash": "0xAB",
        "blockNumber": "26300000",
        "timeStamp": "1793305206",
        "from": "0x" + "1" * 40,
        "to": "0x" + "2" * 40,
        "value": "1000",
        "gasUsed": "21000",
        "gasPrice": "10",
        "isError": "0",
        "txreceipt_status": "1",
        "methodId": "0x",
    }
    out = etherscan.normal_transfers(Chain.ETHEREUM, row, "0x" + "1" * 40)
    assert [t.kind for t in out] == [Kind.NATIVE, Kind.FEE]
    assert out[0].uid == "ethereum:0xab:n" and out[0].amount == 1000
    assert out[1].amount == 210_000 and out[1].receiver == "ethereum:@fees"
    # The receiver of the same transaction pays no fee.
    assert [t.kind for t in etherscan.normal_transfers(Chain.ETHEREUM, row, "0x" + "2" * 40)] == [Kind.NATIVE]


def test_failed_transaction_moves_no_value_but_burns_the_fee() -> None:
    row = {
        "hash": "0xab",
        "blockNumber": "1",
        "timeStamp": "1",
        "from": "0x" + "1" * 40,
        "to": "0x" + "2" * 40,
        "value": "5",
        "gasUsed": "1",
        "gasPrice": "1",
        "isError": "1",
    }
    assert [t.kind for t in etherscan.normal_transfers(Chain.ETHEREUM, row, "0x" + "1" * 40)] == [Kind.FEE]


def test_token_ids_do_not_depend_on_the_provider() -> None:
    base = {
        "hash": "0xAB",
        "blockNumber": "1",
        "timeStamp": "1",
        "from": "0x" + "1" * 40,
        "to": "0x" + "2" * 40,
        "contractAddress": "0x" + "a" * 40,
        "value": "7",
        "tokenSymbol": "USDT",
        "tokenDecimal": "6",
    }
    with_log, _ = etherscan.token_transfer(Chain.ETHEREUM, {**base, "logIndex": "12"}, 0)
    without_log, _ = etherscan.token_transfer(Chain.ETHEREUM, base, 0)
    assert with_log is not None and without_log is not None
    assert with_log.uid == without_log.uid


def test_bitcoin_transaction_becomes_a_node_with_inputs_outputs_and_fee() -> None:
    tx = {
        "txid": "ff",
        "status": {"confirmed": True, "block_height": 973732, "block_time": 1793305206},
        "fee": 500,
        "vin": [{"prevout": {"scriptpubkey_address": "bc1q0mtc2f5agpaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "value": 1500}}],
        "vout": [
            {"scriptpubkey_address": "bc1q0ts8gaf9ht0rr4u02hr56f8thndqsc4kmkmfv3", "value": 1000},
            {"scriptpubkey_type": "op_return", "scriptpubkey_asm": "OP_RETURN OP_PUSHBYTES_6 3d3a4554482e", "value": 0},
        ],
    }
    out = bitcoin.tx_transfers(tx)
    kinds = [(t.kind, t.sender.split(":")[1][:3], t.receiver.split(":")[1][:3]) for t in out]
    assert kinds == [(Kind.UTXO, "bc1", "@tx"), (Kind.UTXO, "@tx", "bc1"), (Kind.FEE, "@tx", "@fe")]
    assert out[0].meta["memo"] == "=:ETH."
    assert bitcoin.tx_transfers({**tx, "status": {"confirmed": False}}) == []


def test_tron_transfer_contract_hex_addresses_become_base58() -> None:
    tx = {
        "ret": [{"contractRet": "SUCCESS", "fee": 0}],
        "txID": "60",
        "blockNumber": 87745685,
        "block_timestamp": 1793910726000,
        "raw_data": {
            "data": "3d3a4254432e425443",
            "contract": [
                {
                    "type": "TransferContract",
                    "parameter": {
                        "value": {
                            "amount": 4,
                            "owner_address": "41b6dde5f23a30fc89bb38e521af35d5c41fdf744b",
                            "to_address": "4189cbcb2372e1c2fbc00f24895a406a0c722c89f3",
                        }
                    },
                }
            ],
        },
        "internal_transactions": [],
    }
    out = tron.tx_transfers(tx, "x")
    assert len(out) == 1 and out[0].sender.startswith("tron:T") and out[0].ts == 1793910726
    assert out[0].meta["memo"] == "=:BTC.BTC"


def test_xrpl_uses_delivered_amount_and_keeps_the_destination_tag() -> None:
    item = {
        "meta": {"TransactionResult": "tesSUCCESS", "delivered_amount": "99650000", "TransactionIndex": 3},
        "tx": {
            "Account": "rnaP31XTDR6ZB8smq3FN1evX8Ev7wdoANY",
            "Destination": "rEb8TK3gBgk5auZkwc6sHnwrGVJH8DuaLh",
            "Amount": "999999999",
            "DestinationTag": 104849694,
            "Fee": "15",
            "TransactionType": "Payment",
            "hash": "AA",
            "ledger_index": 108073000,
            "date": 846000000,
        },
    }
    out, _ = xrpl.tx_transfers(item, "rnaP31XTDR6ZB8smq3FN1evX8Ev7wdoANY")
    assert out[0].amount == 99_650_000 and out[0].meta["tag"] == "104849694"
    assert out[0].ts == 846000000 + 946_684_800
    assert out[1].kind is Kind.FEE


def test_solana_token_transfers_run_between_owners_not_token_accounts() -> None:
    owner_a, owner_b = "AffzFDKZbyMiU11zmVBoAqD25ugpr7xoWWyRyGigBEaU", "24xu7h76wz75DaUkLcGrMTJzoMF6DVcNtz8FjQ5KWwni"
    acct_a, acct_b = "9FmVtgPZkMWYE1LjRYcSZfbRMesqnubw95j66s5WuaPN", "HTYwjSV4A9nTwqFt8cXAAy8GMDS5eR7qCiHuWNAJtbEc"
    mint = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
    tx = {
        "slot": 1,
        "blockTime": 1793305206,
        "transaction": {
            "message": {
                "accountKeys": [{"pubkey": owner_a}, {"pubkey": acct_a}, {"pubkey": acct_b}],
                "instructions": [
                    {
                        "program": "spl-token",
                        "parsed": {
                            "type": "transferChecked",
                            "info": {
                                "source": acct_a,
                                "destination": acct_b,
                                "mint": mint,
                                "tokenAmount": {"amount": "5000000", "decimals": 6},
                            },
                        },
                    }
                ],
            }
        },
        "meta": {
            "err": None,
            "fee": 5000,
            "innerInstructions": [],
            "preTokenBalances": [
                {"accountIndex": 1, "mint": mint, "owner": owner_a, "uiTokenAmount": {"decimals": 6}},
                {"accountIndex": 2, "mint": mint, "owner": owner_b, "uiTokenAmount": {"decimals": 6}},
            ],
            "postTokenBalances": [],
        },
    }
    out, tokens = solana.tx_transfers(tx, "sig", owner_a)
    assert out[0].sender == f"solana:{owner_a}" and out[0].receiver == f"solana:{owner_b}"
    assert out[0].amount == 5_000_000 and tokens[0].decimals == 6
    assert out[1].kind is Kind.FEE


def test_thorchain_swap_becomes_a_cross_chain_link_in_native_units() -> None:
    action = {
        "status": "success",
        "type": "swap",
        "date": "1793990646906643637",
        "in": [{"address": "0x" + "1" * 40, "txID": "AB", "coins": [{"asset": "ETH.ETH", "amount": "100000000"}]}],
        "out": [
            {
                "address": "bc1q0ts8gaf9ht0rr4u02hr56f8thndqsc4kmkmfv3",
                "txID": "CD",
                "coins": [{"asset": "BTC.BTC", "amount": "3200000"}],
            }
        ],
        "metadata": {"swap": {"inPriceUSD": "2400", "outPriceUSD": "75000", "memo": "=:BTC.BTC:bc1q..."}},
    }
    [link] = thorchain.links(action)
    assert link.kind is Kind.BRIDGE and link.chain is Chain.BITCOIN and link.amount == 3_200_000
    assert link.sender == "ethereum:0x" + "1" * 40
    assert link.meta["in_tx"] == "AB"
    assert thorchain.links({**action, "status": "pending"}) == []


def test_thorchain_asset_parsing_restores_tron_base58() -> None:
    chain, asset, symbol = thorchain.parse_asset("TRON.USDT-TR7NHQJEKQXGTCI8Q8ZY4PL8OTSZGJLJ6T")
    assert chain is Chain.TRON and asset == "tron:TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t" and symbol == "USDT"
    assert thorchain.parse_asset("ETH~ETH")[0] is None  # trade assets live inside THORChain
    assert thorchain.is_swap_memo("=:BTC.BTC:bc1q") and not thorchain.is_swap_memo("hello")
    assert thorchain.midgard_txid("0xabc") == "ABC"


def test_relay_request_becomes_a_cross_chain_link() -> None:
    from goldenhour.chains import relay

    request = {
        "id": "0xabc",
        "status": "success",
        "user": "0x" + "1" * 40,
        "recipient": "TG5iBMQPrDzJJAUM8GMVBf1hRaJ1wvzsGi",
        "createdAt": "2026-10-30T02:30:00.000Z",
        "data": {
            "inTxs": [{"hash": "0xIN", "chainId": 1}],
            "outTxs": [{"hash": "abcd", "chainId": 728126428, "timestamp": 1793327400}],
            "metadata": {
                "currencyIn": {
                    "currency": {"chainId": 1, "address": "0x" + "0" * 40, "decimals": 18},
                    "amount": "1000000000000000000",
                    "amountUsd": "2400.5",
                },
                "currencyOut": {
                    "currency": {
                        "chainId": 728126428,
                        "address": "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t",
                        "symbol": "USDT",
                        "decimals": 6,
                    },
                    "amount": "2390000000",
                },
            },
        },
    }
    [link] = relay.links(request)
    assert link.kind is Kind.BRIDGE and link.chain is Chain.TRON
    assert link.receiver == "tron:TG5iBMQPrDzJJAUM8GMVBf1hRaJ1wvzsGi"
    assert link.asset == "tron:TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t" and link.amount == 2_390_000_000
    assert link.meta["usd"] == "2400.5" and link.ts == 1793327400
    assert relay.links({**request, "status": "refund"}) == []


async def test_each_history_list_resumes_from_its_own_cursor_less_a_margin() -> None:
    from goldenhour.chains.etherscan import MARGIN_BLOCKS, EtherscanAdapter

    reads: dict[str, int] = {}
    rows = {
        "txlist": [{"blockNumber": "1000", "timeStamp": "1", "hash": "0xa", "from": "0x1", "to": "0x2", "value": "0"}],
        "txlistinternal": [],  # indexed late: nothing yet past its cursor
        "tokentx": [],
    }

    async def walk(action: str, address: str, start: int, end: int = 999_999_999) -> list[dict[str, str]]:
        reads[action] = start
        return rows[action]

    adapter = EtherscanAdapter.__new__(EtherscanAdapter)
    adapter.chain = Chain.ETHEREUM
    adapter._walk = walk  # type: ignore[method-assign]
    page = await adapter.history("0x" + "1" * 40, lists={"normal": 900, "internal": 700, "token": 800})
    assert reads == {
        "txlist": 900 - MARGIN_BLOCKS,
        "txlistinternal": 700 - MARGIN_BLOCKS,
        "tokentx": 800 - MARGIN_BLOCKS,
    }
    # The internal list stays where it was, so a row it indexes late (block 950, say) is still read.
    assert page.cursor["lists"] == {"normal": 1000, "internal": 700, "token": 800}
    # A cursor saved before per-list cursors applies to all three lists.
    await adapter.history("0x" + "1" * 40, start_block=500)
    assert set(reads.values()) == {500 - MARGIN_BLOCKS}


def _xrpl_item(kind: str, account: str, nodes: list[dict], **fields: object) -> dict:
    return {
        "meta": {"TransactionResult": "tesSUCCESS", "TransactionIndex": 1, "AffectedNodes": nodes},
        "tx": {
            "Account": account,
            "Fee": "12",
            "TransactionType": kind,
            "hash": "H",
            "ledger_index": 1,
            "date": 0,
            **fields,
        },
    }


def _root(account: str, before: int, after: int, kind: str = "ModifiedNode") -> dict:
    return {
        kind: {
            "LedgerEntryType": "AccountRoot",
            "FinalFields": {"Account": account, "Balance": str(after)},
            "PreviousFields": {"Balance": str(before)},
        }
    }


def test_xrpl_ledger_matches_the_metadata_for_trades_and_deletions() -> None:
    a, b = "rK21t6nGLy4MNDqhvy3pGwcLFy25qgfzMx", "rEb8TK3gBgk5auZkwc6sHnwrGVJH8DuaLh"
    usd = "rhub8VRN55s94qWKDv6jmDy1pUykJzF3wq"
    # A sells 1,000 XRP for 2,400 USD on the ledger's exchange: no Payment, only metadata.
    line = {
        "ModifiedNode": {
            "LedgerEntryType": "RippleState",
            "FinalFields": {
                "LowLimit": {"issuer": a},
                "HighLimit": {"issuer": usd},
                "Balance": {"currency": "USD", "value": "2400"},
            },
            "PreviousFields": {"Balance": {"currency": "USD", "value": "0"}},
        }
    }
    item = _xrpl_item("OfferCreate", a, [_root(a, 5_000_000_000, 3_999_999_988), line])
    out, tokens = xrpl.tx_transfers(item, a)
    xrp = [t for t in out if t.asset == "xrpl:native" and t.kind is not Kind.FEE]
    iou = [t for t in out if t.asset == f"xrpl:USD.{usd}"]
    assert [(t.sender, t.receiver, t.amount) for t in xrp] == [(f"xrpl:{a}", xrpl.DEX, 1_000_000_000)]
    assert [(t.sender, t.receiver, t.amount) for t in iou] == [(xrpl.DEX, f"xrpl:{a}", 2400 * 10**15)]
    assert tokens and tokens[0].symbol == "USD"
    # A deletes its account: the rest of its XRP goes to B, and both sides book the same transfer.
    deletion = _xrpl_item(
        "AccountDelete", a, [_root(a, 3_999_999_988, 0, "DeletedNode"), _root(b, 0, 3_999_999_976)], Destination=b
    )
    from_a = [t for t in xrpl.tx_transfers(deletion, a)[0] if t.kind is not Kind.FEE]
    from_b = xrpl.tx_transfers(deletion, b)[0]
    assert [(t.uid, t.sender, t.receiver, t.amount) for t in from_a] == [
        ("xrpl:H:d", f"xrpl:{a}", f"xrpl:{b}", 3_999_999_976)
    ]
    assert [(t.uid, t.amount) for t in from_b] == [("xrpl:H:d", 3_999_999_976)]
    # A plain payment is fully explained by its transfer and fee: nothing extra is booked.
    pay = _xrpl_item(
        "Payment", a, [_root(a, 100_000_012, 0), _root(b, 0, 100_000_000)], Destination=b, Amount="100000000"
    )
    pay["meta"]["delivered_amount"] = "100000000"
    assert [t.uid for t in xrpl.tx_transfers(pay, a)[0]] == ["xrpl:H:p", "xrpl:H:f"]


def _sol_tx(keys: list[str], pre: list[int], post: list[int], instructions: list[dict], **meta: object) -> dict:
    return {
        "slot": 1,
        "blockTime": 1793305206,
        "transaction": {"message": {"accountKeys": [{"pubkey": k} for k in keys], "instructions": instructions}},
        "meta": {
            "err": None,
            "fee": 5000,
            "innerInstructions": [],
            "preBalances": pre,
            "postBalances": post,
            "preTokenBalances": [],
            "postTokenBalances": [],
            **meta,
        },
    }


def _ix(program: str, kind: str, **info: object) -> dict:
    return {"program": program, "parsed": {"type": kind, "info": info}}


def test_solana_wrapped_sol_is_sol_and_wrapping_is_not_a_movement() -> None:
    # The attacker wraps 100 SOL in a temporary account, swaps it into a pool and closes the account.
    me, temp, pool, vault = (
        "AffzFDKZbyMiU11zmVBoAqD25ugpr7xoWWyRyGigBEaU",
        "9FmVtgPZkMWYE1LjRYcSZfbRMesqnubw95j66s5WuaPN",
        "24xu7h76wz75DaUkLcGrMTJzoMF6DVcNtz8FjQ5KWwni",
        "HTYwjSV4A9nTwqFt8cXAAy8GMDS5eR7qCiHuWNAJtbEc",
    )
    rent, sol = 2_039_280, 100 * 10**9
    tx = _sol_tx(
        [me, temp, vault],
        pre=[sol + rent + 10_000, 0, 5_000 * 10**9],
        post=[rent + 5_000, 0, 5_100 * 10**9],  # the rent came back when the account closed
        instructions=[
            _ix("system", "createAccount", source=me, newAccount=temp, lamports=sol + rent),
            _ix("spl-token", "initializeAccount3", account=temp, mint=solana.WSOL, owner=me),
            _ix(
                "spl-token",
                "transferChecked",
                source=temp,
                destination=vault,
                mint=solana.WSOL,
                tokenAmount={"amount": str(sol), "decimals": 9},
            ),
            _ix("spl-token", "closeAccount", account=temp, destination=me, owner=me),
        ],
        preTokenBalances=[{"accountIndex": 2, "mint": solana.WSOL, "owner": pool, "uiTokenAmount": {"decimals": 9}}],
    )
    out, tokens = solana.tx_transfers(tx, "sig", me)
    moves = [(t.sender, t.receiver, t.asset, t.amount) for t in out if t.kind is not Kind.FEE]
    # One movement: 100 SOL from the attacker to the pool. The rent came back; nothing is left over.
    assert moves == [(f"solana:{me}", f"solana:{pool}", "solana:native", sol)]
    assert tokens == []


def test_solana_sol_paid_by_a_program_is_booked_from_the_metadata() -> None:
    me, program_account = "AffzFDKZbyMiU11zmVBoAqD25ugpr7xoWWyRyGigBEaU", "24xu7h76wz75DaUkLcGrMTJzoMF6DVcNtz8FjQ5KWwni"
    tx = _sol_tx([program_account, me], pre=[9 * 10**9, 0], post=[4 * 10**9 - 5000, 5 * 10**9], instructions=[])
    out, _ = solana.tx_transfers(tx, "sig", me)
    assert [(t.sender, t.receiver, t.amount) for t in out] == [(solana.PROGRAM, f"solana:{me}", 5 * 10**9)]


async def test_a_query_the_explorer_cannot_serve_is_narrowed() -> None:
    import itertools

    import httpx
    import respx

    from goldenhour.chains.etherscan import EXPLORERS, EtherscanAdapter
    from goldenhour.http import PoliteClient

    explorer = EXPLORERS[Chain.ETHEREUM][0]
    windows: list[tuple[int, int]] = []

    def answer(request: httpx.Request) -> httpx.Response:
        start, end = int(request.url.params["startblock"]), int(request.url.params["endblock"])
        windows.append((start, end))
        if end - start > 2_000:  # what Routescan does for a busy address over a wide range
            return httpx.Response(200, json={"status": "0", "message": "An error occurred", "result": None})
        rows = [{"hash": f"0x{b:x}", "blockNumber": str(b)} for b in (1_000, 5_000, 9_000) if start <= b <= end]
        return httpx.Response(200, json={"status": "1", "message": "OK", "result": rows})

    with respx.mock(assert_all_called=False) as mock:
        mock.get(url__startswith=explorer.base_url).mock(side_effect=answer)
        async with PoliteClient(rates={"api.routescan.io": 1000.0}, retries=0) as client:
            rows = await EtherscanAdapter(client, explorer)._walk("txlist", "0x" + "1" * 40, 0, 10_000)
    assert [r["blockNumber"] for r in rows] == ["1000", "5000", "9000"]  # nothing lost between windows
    assert windows[0] == (0, 10_000)  # the whole range first, then narrower windows
    # Every block of the range is covered by a window that succeeded.
    served = sorted((s, e) for s, e in windows if e - s <= 2_000)
    assert served[0][0] == 0 and served[-1][1] == 10_000
    assert all(b[0] == a[1] + 1 for a, b in itertools.pairwise(served))


async def test_a_busy_address_is_read_by_block_never_by_deep_pages() -> None:
    import httpx
    import respx

    from goldenhour.chains import etherscan
    from goldenhour.chains.etherscan import EXPLORERS, EtherscanAdapter
    from goldenhour.http import PoliteClient

    explorer = EXPLORERS[Chain.ETHEREUM][0]
    # 500 rows in each of blocks 10 to 14, 1,500 in block 20, and two identical rows in one tx.
    chain_rows = [{"hash": f"0x{b}-{i}", "blockNumber": str(b)} for b in range(10, 15) for i in range(500)]
    chain_rows += [{"hash": f"0x20-{i}", "blockNumber": "20"} for i in range(1_500)]
    chain_rows += [{"hash": "0xtwin", "blockNumber": "30", "value": "1"}] * 2
    asked: list[tuple[int, int, int]] = []

    def answer(request: httpx.Request) -> httpx.Response:
        p = request.url.params
        start, end, page, size = int(p["startblock"]), int(p["endblock"]), int(p["page"]), int(p["offset"])
        asked.append((start, end, page))
        if page > 1 and start != end:
            return httpx.Response(200, json={"status": "0", "message": "An error occurred", "result": None})
        hits = [r for r in chain_rows if start <= int(r["blockNumber"]) <= end]
        return httpx.Response(
            200, json={"status": "1", "message": "OK", "result": hits[(page - 1) * size : page * size]}
        )

    with respx.mock(assert_all_called=False) as mock:
        mock.get(url__startswith=explorer.base_url).mock(side_effect=answer)
        async with PoliteClient(rates={"api.routescan.io": 1000.0}, retries=0) as client:
            rows = await EtherscanAdapter(client, explorer)._walk("txlist", "0x" + "1" * 40, 0, 100)
    assert len(rows) == len(chain_rows)  # every row once, the identical pair included
    assert sorted(r["hash"] for r in rows) == sorted(r["hash"] for r in chain_rows)
    assert all(page == 1 or start == end for start, end, page in asked)  # deep pages only inside one block
    assert etherscan.PAGE_SIZE == 1000


async def test_pages_shrink_when_the_explorer_cannot_serve_big_ones() -> None:
    import httpx
    import respx

    from goldenhour.chains.etherscan import EXPLORERS, EtherscanAdapter
    from goldenhour.http import PoliteClient

    explorer = EXPLORERS[Chain.ETHEREUM][0]
    chain_rows = [{"hash": f"0x{b}-{i}", "blockNumber": str(b)} for b in range(100) for i in range(30)]
    sizes: set[int] = set()

    def answer(request: httpx.Request) -> httpx.Response:
        p = request.url.params
        start, end, page, size = int(p["startblock"]), int(p["endblock"]), int(p["page"]), int(p["offset"])
        if size > 250:  # this busy address: big pages always fail, whatever the range
            return httpx.Response(200, json={"status": "0", "message": "An error occurred", "result": None})
        sizes.add(size)
        hits = [r for r in chain_rows if start <= int(r["blockNumber"]) <= end]
        return httpx.Response(
            200, json={"status": "1", "message": "OK", "result": hits[(page - 1) * size : page * size]}
        )

    with respx.mock(assert_all_called=False) as mock:
        mock.get(url__startswith=explorer.base_url).mock(side_effect=answer)
        async with PoliteClient(rates={"api.routescan.io": 1000.0}, retries=0) as client:
            rows = await EtherscanAdapter(client, explorer)._walk("txlist", "0x" + "1" * 40, 0, 99)
    assert sorted(r["hash"] for r in rows) == sorted(r["hash"] for r in chain_rows)
    assert sizes and max(sizes) <= 250


async def test_a_replay_can_skip_a_block_the_explorer_never_serves(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import asyncio

    import httpx
    import pytest
    import respx

    from goldenhour.chains.etherscan import EXPLORERS, EtherscanAdapter, QueryTooHeavy
    from goldenhour.http import PoliteClient

    explorer = EXPLORERS[Chain.ETHEREUM][0]
    chain_rows = [{"hash": f"0x{b}-{i}", "blockNumber": str(b)} for b in range(10) for i in range(5)]
    real_sleep = asyncio.sleep
    monkeypatch.setattr("goldenhour.chains.etherscan.asyncio.sleep", lambda s: real_sleep(0))

    def answer(request: httpx.Request) -> httpx.Response:
        p = request.url.params
        start, end, page, size = int(p["startblock"]), int(p["endblock"]), int(p["page"]), int(p["offset"])
        if start <= 7 <= end:  # block 7 breaks every query that includes it
            return httpx.Response(200, json={"status": "0", "message": "An error occurred", "result": None})
        hits = [r for r in chain_rows if start <= int(r["blockNumber"]) <= end][(page - 1) * size : page * size]
        return httpx.Response(200, json={"status": "1", "message": "OK", "result": hits})

    with respx.mock(assert_all_called=False) as mock:
        mock.get(url__startswith=explorer.base_url).mock(side_effect=answer)
        async with PoliteClient(rates={"api.routescan.io": 1000.0}, retries=0) as client:
            strict = EtherscanAdapter(client, explorer)
            with pytest.raises(QueryTooHeavy):
                await strict._walk("txlist", "0x" + "1" * 40, 0, 9)
            lenient = EtherscanAdapter(client, explorer)
            lenient.skip_unreadable = True
            rows = await lenient._walk("txlist", "0x" + "1" * 40, 0, 9)
    assert len(rows) == 45 and lenient.unread == [("txlist", 7)]
