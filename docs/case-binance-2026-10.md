# The Binance theft, read from the chain

What the public blockchains show about the theft of 29 October 2026, as Goldenhour reconstructed it.
Every statement below links to the transaction that proves it, and none of it depends on a press
report. Where a press report supplies something the chain cannot (how the attackers got in), the
source is named. The live numbers are on the [site](https://dharmeshdev1.github.io/goldenhour/); the
method is in [method.md](method.md).

## How the money left

All times UTC, 29 October 2026.

| Time | Chain | What left | Where it went first |
| --- | --- | --- | --- |
| 20:05:00 to 20:09:20 | XRP Ledger | 233,461,943.1 XRP from five Binance accounts (XRPScan labels) | `rK21t6nGLy4MNDqhvy3pGwcLFy25qgfzMx` |
| 20:10:18 | Solana | 970,521.9 SOL from a Binance wallet, in five transfers | `AffzFDKZbyMiU11zmVBoAqD25ugpr7xoWWyRyGigBEaU` (FBI-listed) |
| 20:20:00 to 20:22:45 | Tron | 10,000 TRX first (20:20:00), then 46,384,098 TRX (20:21:12) and 612,729,468 USDT (20:22:45), from a Binance wallet | `TG5iBMQPrDzJJAUM8GMVBf1hRaJ1wvzsGi` |
| 20:20:06 | Bitcoin | 18,270.2 BTC from `bc1qm34lsc65zpw79lxes69zkqmk6ee3ewf0j77s3h`, in one block | `bc1q0mtc2f5agpkvlq6hhahmfr3hlr55kyfjhl2mnm`, which split it into the 19 FBI-listed addresses in the same block |
| 20:24:23 onward | Ethereum | Swept out of Binance 14 by the attacker's contract; sent from Binance 16 at 20:24:47 | `0x5227f603c1ee34ce3c06efe6477003bdfccb2f8c`, and from 22:39:35 `0xa97bf7836f8a08f9ed91d41453d371493fae8d10` |

The FBI's list (31 October) names mostly second hops and later. The first hops above were found by
walking back from the FBI's addresses inside the theft window, and by reading the attacker's own
transactions.

## The delegation

EIP-7702 lets an ordinary account run a contract's code. An account can only delegate if its own
key signs an authorization for a specific nonce.

- **15:59:11** The attacker's executor account `0x7C8601d4645b93cea45F71e118A729332e6B5CA0` deploys a
  sweeper contract, `0x63e842B054Bd624a465454D533C33F573e9c6fAe`
  ([tx](https://etherscan.io/tx/0x97408e7562eb2b28efcd24ba01609607b5749bc89448d007fa8b50ecf290329c)).
- **13:51 and 16:20 to 16:26** Rehearsals: the same executor delegates test wallets, the second
  time to the same sweeper
  ([tx](https://etherscan.io/tx/0xb2148eb825cac75efe0ae7fb5c319e6b66df62631738ba252185960e50dc5782)).
- **20:20:59** One transaction carries 50 authorizations, all signed by Binance 14's key, for 50
  consecutive nonces, 18,914,775 to 18,914,824
  ([tx](https://etherscan.io/tx/0xad99264d5532ae0dd04b2004eebda05f25657e70dacb764f7ac8d4ccd051298b)).
  Goldenhour recovers the signer of every one of them with its own code: it is Binance 14 each
  time. Binance 14 sends thousands of transactions a day, so its nonce moves constantly; signing a
  run of nonces guaranteed that one would match whenever the transaction landed. Binance 14's own
  transaction with nonce 18,914,808 was five places earlier in the same block, so nonce 18,914,809
  took effect and 49 were rejected.
- From then on, a call to Binance 14 runs the sweeper's code with Binance 14's balances. The
  executor calls `sweep(address,address[])` (selector `0x8a179be4`) on Binance 14 twelve times
  between 20:24:23 and 23:56:47. The first sweep alone moved about 167,000 ETH and 63 tokens
  ([tx](https://etherscan.io/tx/0x53bd38b816c23e74134786c29a8c18d792777998a3b9326047b583100e88e2f3)).

The reports say the signing service "signed what the attacker requested". On-chain, that is exactly
50 signatures over EIP-7702 authorization messages for one custody wallet. A policy that refuses to
sign any delegation for a custody wallet ends the attack there: see the
[Guard case](https://dharmeshdev1.github.io/goldenhour/guard/).

## The allowance

At **20:41:23** 823,000,000 USDC left a different Binance wallet, `0xee7ae85f2fe2239e27d9c1e23fffe168d63b4055`,
in a call made to Binance 14
([tx](https://etherscan.io/tx/0xdc102dd89dded5a83f1008906883ffb7c81ff3b7d7965d8c7277d170b5e17481)).
That wallet had granted Binance 14 an allowance. Once Binance 14 ran the attacker's code, every
approval anyone had granted to it became a way out. Circle froze the receiving address at 21:04:59,
before any of the USDC was swapped.

## The freeze race

Read from the issuers' own contract events:

- **21:01:35** Tether's first freezes on Ethereum, including `0x5227f603...`, the first hop that had
  received the swept USDT. By then 434 million USDT had been swapped for ETH (Elliptic).
- **21:04:59** Circle freezes the address holding the 823 million USDC.
- **21:09:12** Tether freezes `TG5iBMQ...` on Tron, holding 312.7 million USDT; 300 million had moved
  on 24 minutes earlier. Elliptic reports that 234 million USDT on Tron was swapped for TRX before
  the freezes landed.
- **22:06:23** Tether freezes Binance 14 itself; Circle follows at 23:53:35. The wallet was still
  running the attacker's code.

The [golden hour page](https://dharmeshdev1.github.io/goldenhour/golden-hour/) plots this minute by
minute, with what a faster response would have kept.

## What Goldenhour does not know

How the attackers reached the signing service (a tampered Homebrew package, fake interviews, an
internal AI tool and a stolen deployment token) comes from Binance's report as quoted by the press.
The chain shows the result, not the intrusion. Attribution to TraderTraitor is the FBI's.
