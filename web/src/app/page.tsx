import { BigNumbers } from "@/components/war-room/big-numbers";
import { Hero } from "@/components/war-room/hero";
import { LayersStrip } from "@/components/war-room/layers-strip";
import { LiveFeed } from "@/components/war-room/live-feed";
import { WhereNow } from "@/components/war-room/where-now";

export default function WarRoomPage() {
  return (
    <>
      <Hero />
      <BigNumbers />
      <LayersStrip />
      <WhereNow />
      <LiveFeed />
    </>
  );
}
