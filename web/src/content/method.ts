/**
 * The methodology page, as editable content. Each section is a list of blocks; text supports
 * **bold**, `code`, [links](/path/ or https://...) and source citations like {src:fbi}, which
 * link to the source with that id in case.json. Internal links start with "/".
 * Keep it accurate to what the pipeline does; when the pipeline changes, change this file.
 */

export type MethodBlock =
  | { type: "p"; text: string }
  | { type: "list"; items: string[]; ordered?: boolean }
  | { type: "code"; text: string; label?: string }
  | { type: "note"; text: string };

export interface MethodSection {
  id: string;
  title: string;
  blocks: MethodBlock[];
}

export const METHOD_INTRO =
  "How Goldenhour turns public blockchain data into the figures on this site, what the trail can and cannot see, and how to check any number yourself.";

export const METHOD: MethodSection[] = [
  {
    id: "data-sources",
    title: "Data sources",
    blocks: [
      {
        type: "p",
        text: "Everything starts from public data. Each chain has an adapter that turns its sources into one shape: an amount of one asset moving from one address to another at one moment.",
      },
      {
        type: "list",
        items: [
          "**EVM chains** (Ethereum, Optimism, Arbitrum, Base, Polygon, Avalanche): explorer APIs that speak the Etherscan `account` module (Etherscan, Blockscout, Routescan), plus Blockscout's REST API, which carries the authorization list of EIP-7702 transactions. That is how a delegation being installed becomes visible.",
          "**Bitcoin**: Esplora, the API behind mempool.space and blockstream.info.",
          "**Tron**: TronGrid, including TRC-20 token transfers.",
          "**XRP Ledger**: public rippled servers. The ledger's `delivered_amount` is used rather than the requested amount, and destination tags are kept, because exchanges share one account across customers.",
          "**Solana**: a public JSON-RPC node, with token transfers rewritten to run between the wallets that own the token accounts.",
          "**Cross-chain**: THORChain through Midgard and Relay through its public request index. Both record the two legs of a swap, so these links are facts rather than guesses.",
          "**Prices**: DefiLlama's coins API, by contract address. A token only counts if it is priced with high confidence, so a spam token named \"USDT\" is worth nothing.",
        ],
      },
      {
        type: "p",
        text: "The pipeline reruns every few minutes and republishes the JSON this site reads. Which sources answered on the last run is listed in `meta.json` and in the footer of every page.",
      },
    ],
  },
  {
    id: "trail-rule",
    title: "The trail rule",
    blocks: [
      { type: "note", text: "An address joins the trail when most of the value it ever received came from the trail." },
      {
        type: "p",
        text: "The walk starts from the seeds: the addresses named by investigators (the FBI published 628 on 31 October 2026 {src:fbi}) and the first hops out of the victim's wallets during the theft window. Each pass looks at every address the trail sent real money to and asks one question: of everything this address has ever received, in dollars at the time, did more than half come from trail addresses? If so, it joins the trail, and the walk continues from it.",
      },
      {
        type: "list",
        items: [
          "Outflows under **$1,000** are not followed. They are dust, address poisoning and fees.",
          "An address that received from the trail but fails the test is recorded as a **counterparty**: it is not on the trail, but exposure to it is real, and screening reports it as direct exposure.",
          "Everything is recomputed from stored transfers on every run. The only memory between runs is which addresses were judged, and why.",
        ],
      },
    ],
  },
  {
    id: "services",
    title: "Services stop the walk",
    blocks: [
      {
        type: "p",
        text: "Exchanges, bridges, pools and mixers hold everyone's money. A tracer that follows stolen value into an exchange's hot wallet would start following every customer of that exchange. So before an address can join the trail it is identified: is it a contract, does a public explorer tag it, and what do the tags say it is.",
      },
      {
        type: "p",
        text: "Value that reaches a service is recorded as an **exit** to that service, by category (exchange, mixer, bridge, DEX, lending, staking, other). That is the \"moved out\" figure on the war room. An exit is where a freeze request or a subpoena goes next, not the end of the story.",
      },
      {
        type: "p",
        text: "Where tags are missing, behavior decides: an address that sends almost all of what it receives to a single exchange looks like a customer deposit address, and is treated as part of that exchange.",
      },
    ],
  },
  {
    id: "bitcoin",
    title: "Bitcoin transaction nodes",
    blocks: [
      {
        type: "p",
        text: "Bitcoin has no accounts. A transaction spends whole outputs of earlier transactions and creates new ones, so who paid whom is many to many. Each transaction becomes a node of its own, with an id like `bitcoin:@tx:<txid>`: every input flows into it and every output flows out of it.",
      },
      {
        type: "p",
        text: "The outputs of a transaction carry the share of its inputs that came from the trail. A peel chain is followed step by step, and an unrelated payment mixed into the same transaction is not credited to the attacker. On the [trail graph](/trail/) these nodes sit between their inputs and outputs.",
      },
    ],
  },
  {
    id: "thorchain",
    title: "THORChain links",
    blocks: [
      {
        type: "p",
        text: "THORChain swaps native coins between chains: send ETH to its vault with a memo naming a bitcoin address, and bitcoin arrives there. Midgard records both ends of every swap, the inbound transaction on the source chain and the outbound transaction on the destination chain.",
      },
      {
        type: "p",
        text: "When a trail address swaps through THORChain, the address that receives the other side joins the trail: the attacker chose it. These links show as gold edges on the graph, and the total is reported as \"converted to bitcoin\" in the summary. Swaps out to chains Goldenhour does not index are recorded as exits to THORChain.",
      },
    ],
  },
  {
    id: "freezes",
    title: "Freezes read from issuer contracts",
    blocks: [
      {
        type: "p",
        text: "Stablecoin issuers freeze by blacklisting an address on the token contract. Goldenhour reads those freeze events and the frozen balances from the contracts themselves (for example `AddedBlackList` on USDT and `Blacklisted` on USDC), so the frozen figure is what the chain records, by issuer, chain and asset, with the time each freeze landed.",
      },
      {
        type: "p",
        text: "Seizures by exchanges and law enforcement happen off-chain and are not in this figure. That is why the published \"frozen or seized\" total can be higher than the frozen value shown here; both are shown side by side on the war room.",
      },
    ],
  },
  {
    id: "limits",
    title: "What we cannot see",
    blocks: [
      {
        type: "list",
        items: [
          "**Inside services.** Once value reaches an exchange, a mixer or a privacy pool, what happens next is in that service's books, not on a public chain. Coinjoins break the link between inputs and outputs by design.",
          "**Chains we do not index.** Value bridged or swapped to chains outside the eleven we follow is recorded as an exit, not followed.",
          "**Off-chain settlement.** Over-the-counter trades settled in cash or across a custodian's books leave no transfer to follow.",
          "**Labels.** Service identification relies on public tags and behavior. A wrong or missing tag can stop the walk too early or let it run too far.",
          "**The last few minutes.** Each run has a time budget, so the newest blocks may not be read yet. The data-through time on every page says how current the figures are.",
          "**Prices.** Values use DefiLlama prices at the time of each transfer for \"at theft\" figures and current prices for \"held now\". Thinly traded tokens are excluded rather than guessed.",
        ],
      },
      {
        type: "note",
        text: "So: an address that is not on the trail is not proven clean, and a figure here is an estimate from public data, not a finding of fact.",
      },
    ],
  },
  {
    id: "verify",
    title: "How to verify any number yourself",
    blocks: [
      {
        type: "list",
        ordered: true,
        items: [
          "Every figure on this site is read from a JSON document under `api/v1`, published by the pipeline. The [API page](/api/) lists them all; open any of them in your browser.",
          "Every transfer behind those figures is in `data/transfers.parquet`, with its chain and transaction hash. Open the hash on the chain's public explorer and compare.",
          "Recompute a total with SQL in your browser on the [trail page](/trail/), for example the value that left the victim's wallets:",
        ],
      },
      {
        type: "code",
        label: "SQL",
        text: "SELECT round(sum(t.usd) / 1e9, 3) AS usd_billions\nFROM transfers t\nJOIN nodes n ON n.node = t.sender\nWHERE n.role = 'victim';",
      },
      {
        type: "p",
        text: "The pipeline itself is open source under Apache-2.0 at [github.com/dharmeshdev1/goldenhour](https://github.com/dharmeshdev1/goldenhour). Run it against the same public sources and the same case file, and compare its output with what this site shows.",
      },
    ],
  },
];
