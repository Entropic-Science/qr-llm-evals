import type { RefObject } from "react";
import type { MapMode } from "./model";

interface MapToolbarProps {
  mode: MapMode;
  onMode: (mode: MapMode) => void;
  /** The zoom readout; the camera writes it without a render. */
  readout: RefObject<HTMLSpanElement | null>;
  unfolded: number;
  onZoomIn: () => void;
  onZoomOut: () => void;
  onFit: () => void;
  onHome: () => void;
  onFoldAll: () => void;
  onReview: () => void;
}

const MODES: readonly { id: MapMode; label: string }[] = [{ id: "canvas", label: "Diagram" }, { id: "outline", label: "Outline" }];

/** The map's controls: which form it takes, the zoom, and the way on to the launch. */
export function MapToolbar({ mode, onMode, readout, unfolded, onZoomIn, onZoomOut, onFit, onHome, onFoldAll, onReview }: MapToolbarProps) {
  return <div className="map-toolbar" role="toolbar" aria-label="Map controls">
    <div className="map-toolbar__group" role="group" aria-label="Map form">
      {MODES.map(({ id, label }) => <button key={id} type="button" className={`map-toolbar__mode${mode === id ? " is-current" : ""}`} aria-pressed={mode === id} onClick={() => onMode(id)}>{label}</button>)}
    </div>
    {mode === "canvas" ? <div className="map-toolbar__group" role="group" aria-label="Zoom">
      <button type="button" className="map-toolbar__button" aria-label="Zoom out" title="Zoom out (−)" onClick={onZoomOut}><span aria-hidden="true">−</span></button>
      <span className="map-toolbar__zoom" ref={readout} aria-hidden="true">100%</span>
      <button type="button" className="map-toolbar__button" aria-label="Zoom in" title="Zoom in (+)" onClick={onZoomIn}><span aria-hidden="true">+</span></button>
      <button type="button" className="map-toolbar__button map-toolbar__button--text" title="Show the whole study (0)" onClick={onFit}>Fit</button>
      <button type="button" className="map-toolbar__button map-toolbar__button--text" title="Back to the question, at reading size" onClick={onHome}>Start</button>
    </div> : null}
    {mode === "canvas" && unfolded > 0 ? <button type="button" className="map-toolbar__button map-toolbar__button--text" onClick={onFoldAll}>Fold all</button> : null}
    <button type="button" className="map-toolbar__review text-button" onClick={onReview}>Review &amp; launch</button>
  </div>;
}
