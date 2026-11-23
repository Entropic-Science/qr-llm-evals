import { useCallback } from "react";
import { loadRefusals } from "../../lib/problems";
import { studyFileName } from "../../lib/study-file";
import { useStudy } from "../../routes/configure/StudyContext";
import { Modal } from "../Modal";

/**
 * The question asked before another study takes the place of one with work that is in no file.
 *
 * Closing the dialog any other way than by an answer is "Cancel": the study on screen stays.
 *
 * "Save current study first" replaces the study only once it is in a file that loads back. What the page can
 * tell beforehand it says beforehand; what the check of the written file says, it says here, with the question
 * still open.
 *
 * The same component asks the one question a save can raise (`pendingOverwrite`): whether the file of an
 * earlier save, which loads, may be overwritten by a study that will not load back. It is asked before the write.
 */
export function ReplaceConfirm() {
  const { pendingReplace, answerReplace, config, fileState, fileMark, canSaveAs, savesWithoutAsking, pendingOverwrite, answerOverwrite } = useStudy();
  const cancel = useCallback(() => answerReplace("cancel"), [answerReplace]);
  const keep = useCallback(() => answerOverwrite("cancel"), [answerOverwrite]);
  if (pendingOverwrite !== null) return <Modal
    open
    onClose={keep}
    eyebrow="Save"
    title={`Overwrite ${pendingOverwrite.name} with a file that will not load?`}
    footer={<div className="replace-confirm__actions">
      <button type="button" className="button button--primary" onClick={keep}>Keep the file as it is</button>
      <button type="button" className="button button--secondary" onClick={() => answerOverwrite("overwrite")}>Overwrite it</button>
    </div>}
  >
    <div className="replace-confirm">
      <p>Save writes to {pendingOverwrite.name}, the file of your last save, without asking where. Nothing has been written yet.</p>
      <p className="prereg-error" role="alert">A file of the study as it stands will not load back. {pendingOverwrite.reason}</p>
      <p className="field-note">&ldquo;Keep the file as it is&rdquo; writes nothing; the study stays on screen as you edited it. &ldquo;Overwrite it&rdquo; replaces what {pendingOverwrite.name} holds with the study as it stands. To keep that file and write the study as it stands to another one, keep the file and choose &ldquo;Save as&hellip;&rdquo; under More.</p>
    </div>
  </Modal>;
  if (pendingReplace === null) return null;
  const incoming = pendingReplace.source === "file" ? `"${pendingReplace.name}"`
    : pendingReplace.source === "share" ? "The study from the share link you opened" : "The Study 1 template";
  const standing = fileState === "changed" && fileMark
    ? `The study you are editing has changed since ${fileMark.name}.`
    : fileState === "saved" && fileMark?.loadsBack === false
      ? `The study you are editing was written to ${fileMark.name}, but that file will not load back as it is.`
      : "The study you are editing is not saved to a file.";
  // What "Save current study first" does is only promised as far as the browser lets the page keep the promise.
  const saves = savesWithoutAsking && fileMark
    ? `does not ask where to save it: it checks that a file of the study will load back, writes it to ${fileMark.name}, the file of your last save, and then replaces the study. If the file would not load back, nothing is written and nothing is replaced. If ${fileMark.name} can no longer be written, it asks where to save instead.`
    : canSaveAs
    ? "asks where to save it, writes the file, checks that the file will load back and then replaces the study. If you close that dialog, or the file will not load back, nothing is replaced."
    : `hands it to the browser as a download named ${studyFileName(config.title)}, checks that its text will load back and then replaces the study. If the text will not load back, nothing is replaced. This browser does not tell the page whether a download was written, so check your downloads before you rely on the file.`;
  // What the page's own check already knows. The server is asked about the written text when the file is saved.
  const refused = loadRefusals(config)[0];
  return <Modal
    open
    onClose={cancel}
    eyebrow="Unsaved work"
    title="Replace the study you are editing?"
    footer={<div className="replace-confirm__actions">
      <button type="button" className="button button--primary" onClick={() => answerReplace("save")}>Save current study first</button>
      <button type="button" className="button button--secondary" onClick={() => answerReplace("replace")}>Replace</button>
      <button type="button" className="button button--secondary" onClick={cancel}>Cancel</button>
    </div>}
  >
    <div className="replace-confirm">
      <p>{incoming} is ready to take its place. {standing}</p>
      {pendingReplace.notSaved
        ? <p className="prereg-error" role="alert">{pendingReplace.keptFile
          ? `Nothing was written, so ${pendingReplace.keptFile} is as it was, and nothing was replaced. A file of the study as it stands will not load back.`
          : "The file was written, but it will not load back as it is, so nothing was replaced."} {pendingReplace.notSaved}</p>
        : refused ? <p className="prereg-error" role="alert">As it stands, a study file of this study will not load back: {refused} Fix that first, or the file that &ldquo;Save current study first&rdquo; writes replaces nothing.</p> : null}
      <p className="field-note">&ldquo;Save current study first&rdquo; {saves} &ldquo;Replace&rdquo; discards the study you are editing. &ldquo;Cancel&rdquo; keeps it and loads nothing.</p>
    </div>
  </Modal>;
}
