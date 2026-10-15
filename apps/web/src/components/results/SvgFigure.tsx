import { useState } from "react";
export function SvgFigure({ url, caption }: { url: string; caption: string }) {
  const [failed, setFailed] = useState(false);
  return (
    <figure className="results-figure">
      {failed ? (
        <p role="status">
          Figure unavailable. The numerical tables remain the source of record.
        </p>
      ) : (
        <img
          src={url}
          alt={caption}
          loading="lazy"
          onError={() => setFailed(true)}
        />
      )}
      <figcaption>{caption}</figcaption>
      <a href={url}>Download SVG</a>
    </figure>
  );
}
