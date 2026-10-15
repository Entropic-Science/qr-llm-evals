import { StreamPane, type StreamRecord } from "./StreamPane";

export function TiledStreams({ streams, showThinking, textSize, count = 4 }: { streams: StreamRecord[]; showThinking: boolean; textSize: "sm" | "md" | "lg"; count?: number }) {
  return <section className="tiled-streams" aria-label="Tiled generation streams">{Array.from({ length: Math.max(count, streams.length) }, (_, index) => <StreamPane key={streams[index]?.sampleRef ?? `empty-${index}`} stream={streams[index]} showThinking={showThinking} textSize={textSize} index={index} />)}</section>;
}
