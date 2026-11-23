import { Component, type ReactNode } from "react";

interface DraftBoundaryProps {
  /** The study being drawn. A new study gets a fresh attempt. */
  resetKey: unknown;
  /** Puts the Study 1 template in place of the study that could not be drawn. */
  onLoadTemplate: () => void;
  children: ReactNode;
}

/**
 * Keeps a draft that cannot be drawn from taking the whole page down with it: the header, with its way to
 * load another study, stays, and this says what happened and offers the template.
 */
export class DraftBoundary extends Component<DraftBoundaryProps, { failedFor: unknown; failed: boolean }> {
  override state = { failedFor: null as unknown, failed: false };

  static getDerivedStateFromError(): { failed: boolean } {
    return { failed: true };
  }

  static getDerivedStateFromProps(props: DraftBoundaryProps, state: { failedFor: unknown; failed: boolean }) {
    if (!state.failed) return { failedFor: props.resetKey };
    // The failure belongs to the study it happened on; another study is drawn afresh.
    return state.failedFor === props.resetKey ? null : { failedFor: props.resetKey, failed: false };
  }

  override render() {
    if (!this.state.failed) return this.props.children;
    return <div className="draft-failure" role="alert">
      <p>This draft could not be drawn. Load the Study 1 template.</p>
      <button className="button button--secondary" type="button" onClick={this.props.onLoadTemplate}>Load the Study 1 template</button>
    </div>;
  }
}
