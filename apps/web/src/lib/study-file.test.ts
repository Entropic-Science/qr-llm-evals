import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
import { vi } from "vitest";
import studyOne from "../../../../packages/contracts/fixtures/study1.json";
import { api, ApiError } from "./api";
import { parseStudyConfig, studyOneTemplate, type WizardStudyConfig } from "./config-store";
import { preregDownloadName } from "./prereg";
import { mulberry32 } from "./prng";
import {
  FILE_MARK_STORAGE_KEY, MAX_STUDY_FILE_BYTES, fileMarkOf, fileStateOf, loadStudyFile, readFileMark, saveStudyFile, studyFileName,
  canPickSaveFile, checkStudyFile, studySnapshot, toStudyYaml, writeFileMark, type FileMark, type LoadOutcome, type StudyFileHandle,
} from "./study-file";

const fixtures = resolve(process.cwd(), "../../packages/contracts/fixtures");

// HOW THE GOLDEN STUDY FILES ARE REGENERATED
//
// packages/contracts/fixtures/study1.study.yaml and study1.designed.study.yaml are what `toStudyYaml` writes for
// study1.json and study1.designed.json. This test file is their generator: run it with UPDATE_STUDY_FIXTURES=1
// and the "regenerate" block below writes both files with the app's own writer, after which the byte comparisons
// further down read them back.
//
//   bash:        cd apps/web && UPDATE_STUDY_FIXTURES=1 pnpm exec vitest run src/lib/study-file.test.ts
//   PowerShell:  cd apps/web; $env:UPDATE_STUDY_FIXTURES = "1"; pnpm exec vitest run src/lib/study-file.test.ts; Remove-Item Env:UPDATE_STUDY_FIXTURES
//
// Do it only when `toStudyYaml` or a source fixture changed on purpose, and look at the diff: the backend loads
// the same two files and pins the hashes of what they load to (backend/tests/test_study_file_roundtrip.py).
// Without the variable nothing is ever written.
//
// The same run writes a third pair, hostile.study.json and hostile.study.yaml: the awkward strings, keys and
// numbers of this file (NASTY_STRINGS, NASTY_KEYS, EDGE_NUMBERS), once as plain ASCII JSON and once as
// `toStudyYaml` writes them. The reader used in this file parses every scalar as JSON, so on its own it cannot
// show that a YAML parser reads these forms the same way. The pair is committed so that PyYAML, the parser the
// server uses, can be asked: `yaml.safe_load` of the .yaml must equal `json.load` of the .json.
const UPDATE_FIXTURES = process.env.UPDATE_STUDY_FIXTURES === "1";
const GOLDENS = [["study1.json", "study1.study.yaml"], ["study1.designed.json", "study1.designed.study.yaml"]] as const;
const HEADER = '# Study file for evals.entropic.science\n# Load it with "Load study file" on the setup page. Edits made by hand are kept, comments are not.\n';
const c = (codePoint: number) => String.fromCodePoint(codePoint);
const BACKSLASH = c(92);

/** The body of a study file, without the two comment lines. */
function body(config: unknown): string {
  const text = toStudyYaml(config);
  expect(text.startsWith(HEADER)).toBe(true);
  return text.slice(HEADER.length);
}

function deepFreeze<T>(value: T): T {
  if (value !== null && typeof value === "object") { Object.values(value).forEach(deepFreeze); Object.freeze(value); }
  return value;
}

/** What a reader gets back: null-valued keys are gone, everything else is as it was. */
function withoutNullKeys(value: unknown): unknown {
  if (Array.isArray(value)) return value.map((item) => withoutNullKeys(item ?? null));
  if (value !== null && typeof value === "object") {
    return Object.fromEntries(Object.entries(value).filter(([, item]) => item !== null && item !== undefined).map(([key, item]) => [key, withoutNullKeys(item)]));
  }
  return value;
}

// A reader for exactly the subset the writer emits: block maps and lists, bare or JSON-quoted keys, and scalars
// that are all valid JSON. It shares no code with the writer, so a round trip through it checks the layout
// (indentation, what sits on a dash line) rather than restating it. It cannot show that a real YAML parser
// agrees: the golden files are committed so the backend can load them with PyYAML, the parser the server uses.
function readStudyYaml(text: string): unknown {
  const lines = text.split("\n").filter((line) => line !== "" && !line.startsWith("#"));
  let at = 0;
  const indentOf = (line: string) => line.length - line.trimStart().length;
  const quotedEnd = (source: string) => {
    for (let i = 1; i < source.length; i++) {
      if (source[i] === BACKSLASH) i++;
      else if (source[i] === '"') return i + 1;
    }
    throw new Error(`unterminated string: ${source}`);
  };
  const splitKey = (source: string): [string, string] | null => {
    if (source.startsWith('"')) {
      const end = quotedEnd(source);
      return source[end] === ":" ? [JSON.parse(source.slice(0, end)) as string, source.slice(end + 1)] : null;
    }
    const bare = /^([A-Za-z_][A-Za-z0-9_]*):(.*)$/.exec(source);
    return bare ? [bare[1]!, bare[2]!] : null;
  };
  const scalar = (source: string): unknown => JSON.parse(source);
  const block = (indent: number): unknown => (lines[at]!.slice(indent).startsWith("- ") ? list(indent) : map(indent));
  const list = (indent: number): unknown[] => {
    const out: unknown[] = [];
    while (at < lines.length && indentOf(lines[at]!) === indent && lines[at]!.slice(indent).startsWith("- ")) {
      const rest = lines[at]!.slice(indent + 2);
      if (rest.startsWith("- ") || splitKey(rest)) {
        lines[at] = `${" ".repeat(indent + 2)}${rest}`;
        out.push(block(indent + 2));
      } else { out.push(scalar(rest)); at++; }
    }
    return out;
  };
  const map = (indent: number): Record<string, unknown> => {
    const out: Record<string, unknown> = {};
    while (at < lines.length && indentOf(lines[at]!) === indent && !lines[at]!.slice(indent).startsWith("- ")) {
      const entry = splitKey(lines[at]!.slice(indent));
      if (!entry) throw new Error(`not a map entry: ${lines[at]}`);
      at++;
      const [key, rest] = entry;
      if (rest === "") out[key] = block(indent + 2);
      else { expect(rest.startsWith(" ")).toBe(true); out[key] = scalar(rest.slice(1)); }
    }
    return out;
  };
  if (lines.length === 1 && lines[0] === "{}") return {};
  const result = map(0);
  expect(at).toBe(lines.length);
  return result;
}

const NASTY_STRINGS = [
  "", " ", "  leading and trailing  ", "key: value", "a #comment", "# starts as a comment", "- leading dash", "-", "--- document", "...",
  'double "quotes"', "single 'quotes'", `back${BACKSLASH}slash`, BACKSLASH, `${BACKSLASH}n is not a newline`, `ends with a backslash${BACKSLASH}`, `${BACKSLASH}"`,
  "line\nbreak", "crlf\r\nbreak", "tab\there", "trailing newline\n", "\n\nleading newlines", "three\nlines\nof text",
  "null", "~", "true", "false", "yes", "no", "on", "off", "y", "n", "Null", "TRUE", "123", "0x1F", "0o17", "010", "1e3", "1.5", ".inf", "-.inf", ".nan", "+1", "1_000",
  "2026-09-14", "2026-09-14T00:00:00Z", "12:30:45", "<<", "=", "!!str x", "!tag", "&anchor", "*alias", "@at", "`tick", "%directive", "? question", "| pipe", "> fold",
  "{flow: map}", "[flow, list]", ", comma", "ends with a colon:", ": starts with a colon", "é ü ñ ß", "Study 1 — quantum", "量子ランダム", "עברית", `emoji ${c(0x1f600)} pair`, `astral ${c(0x1d518)}`,
  c(0), c(1), c(7), c(8), c(0x0b), c(0x0c), c(0x1b), c(0x1f), c(0x7f), c(0x80), c(0x85), c(0x9f), c(0xa0), c(0x2028), c(0x2029), c(0xfeff), c(0xfffd), c(0xfffe), c(0xffff),
  `nel${c(0x85)}between`, `separator${c(0x2028)}between`, `paragraph${c(0x2029)}between`, `mark${c(0xfeff)}inside`, "x".repeat(5000),
];

const NASTY_KEYS = [
  "plain_key", "qr-llm", "openai:gpt-5", "has space", "has#hash", "has: colon", "-dash", "9lives", "", "on", "off", "yes", "no", "y", "n", "true", "False", "null", "NULL", "~",
  "é", "a.b", "a\nb", 'q"q', `separator${c(0x2028)}`, `back${BACKSLASH}slash`, "_", "_under", "A1",
];

/** Numbers at the edges of what the writer prints: exponent forms, the largest exact integer, negative zero's neighbours. */
const EDGE_NUMBERS = [0, -1, 1.5, -0.25, 1e21, 1e-7, 1.5e-7, 5e-324, 1.7976931348623157e308, 9007199254740991, -9007199254740991, 0.1, 100, 1e3, 123456789.125];

/**
 * Everything awkward in one value, with nothing a reader would drop: no map key holds null. Read back by any
 * YAML parser it must be this value again.
 */
function hostileValue(): Record<string, unknown> {
  return {
    title: "Awkward strings, keys and numbers, as a study file would hold them",
    strings: NASTY_STRINGS,
    by_key: Object.fromEntries(NASTY_KEYS.map((key, index) => [key, index])),
    keyed: Object.fromEntries(NASTY_KEYS.map((key) => [key, { text: key, list: [key, [key]] }])),
    numbers: EDGE_NUMBERS,
    flags: [true, false],
    empty: { list: [], map: {}, text: "" },
    deep: { a: [{ b: [[1], {}, [[]], ["x", { y: [] }]] }] },
  };
}

/** The value as ASCII JSON: every character above DEL as a \u escape, so the file is plain text to every tool. */
function asciiJson(value: unknown): string {
  return `${JSON.stringify(value, null, 1).replace(/[\u007f-\uffff]/g, (character) => `\\u${character.charCodeAt(0).toString(16).padStart(4, "0")}`)}\n`;
}

const HOSTILE = ["hostile.study.json", "hostile.study.yaml"] as const;

describe.runIf(UPDATE_FIXTURES)("golden study files: regenerate (UPDATE_STUDY_FIXTURES=1)", () => {
  it.each(GOLDENS)("writes %s as %s", (source, target) => {
    const yaml = toStudyYaml(JSON.parse(readFileSync(resolve(fixtures, source), "utf8")));
    expect(yaml.includes("\r")).toBe(false);
    writeFileSync(resolve(fixtures, target), yaml, "utf8");
  });

  it("writes the awkward strings, keys and numbers as hostile.study.json and hostile.study.yaml", () => {
    const yaml = toStudyYaml(hostileValue());
    expect(yaml.includes("\r")).toBe(false);
    writeFileSync(resolve(fixtures, HOSTILE[0]), asciiJson(hostileValue()), "utf8");
    writeFileSync(resolve(fixtures, HOSTILE[1]), yaml, "utf8");
  });
});

describe("toStudyYaml: the awkward values a YAML parser is asked about", () => {
  it("has both files of the pair committed, the JSON in ASCII and the YAML without a carriage return", () => {
    for (const name of HOSTILE) expect(existsSync(resolve(fixtures, name)), name).toBe(true);
    expect(readFileSync(resolve(fixtures, HOSTILE[0]), "utf8")).toMatch(/^[\u0000-\u007e]*$/);
    expect(readFileSync(resolve(fixtures, HOSTILE[1])).includes(13)).toBe(false);
  });

  it("commits exactly what this file's awkward values are, so a new one cannot be left out of the pair", () => {
    expect(JSON.parse(readFileSync(resolve(fixtures, HOSTILE[0]), "utf8"))).toEqual(hostileValue());
    expect(readFileSync(resolve(fixtures, HOSTILE[0]), "utf8")).toBe(asciiJson(hostileValue()));
  });

  it("writes them byte for byte as the committed hostile.study.yaml, which the backend loads with PyYAML", () => {
    const golden = readFileSync(resolve(fixtures, HOSTILE[1]));
    expect(Buffer.from(toStudyYaml(hostileValue()), "utf8").equals(golden)).toBe(true);
    // The value has nothing a reader drops, so this file's own reader gives it back whole.
    expect(readStudyYaml(golden.toString("utf8"))).toEqual(hostileValue());
  });
});

describe("toStudyYaml: the golden study file", () => {
  it("has a generator for every golden it compares", () => {
    for (const [source, target] of GOLDENS) {
      expect(existsSync(resolve(fixtures, source))).toBe(true);
      expect(existsSync(resolve(fixtures, target))).toBe(true);
    }
  });

  it("writes Study 1 byte for byte as the committed study1.study.yaml", () => {
    const golden = readFileSync(resolve(fixtures, "study1.study.yaml"));
    expect(golden.includes(13)).toBe(false);
    const written = Buffer.from(toStudyYaml(studyOne), "utf8");
    expect(written.equals(golden)).toBe(true);
    expect(written.toString("utf8")).toBe(golden.toString("utf8"));
  });

  // The designed fixture and its study file arrive with the design block; once both are committed they are pinned.
  it.skipIf(!existsSync(resolve(fixtures, "study1.designed.json")) || !existsSync(resolve(fixtures, "study1.designed.study.yaml")))("writes the designed Study 1 byte for byte as study1.designed.study.yaml", () => {
    const designed: unknown = JSON.parse(readFileSync(resolve(fixtures, "study1.designed.json"), "utf8"));
    const golden = readFileSync(resolve(fixtures, "study1.designed.study.yaml"));
    expect(golden.includes(13)).toBe(false);
    expect(Buffer.from(toStudyYaml(designed), "utf8").equals(golden)).toBe(true);
    expect(toStudyYaml(designed)).toContain("\ndesign:\n");
  });

  it("leaves `design` out of a legacy study, with every other null", () => {
    const text = toStudyYaml({ ...studyOne, design: null });
    expect(text).toBe(toStudyYaml(studyOne));
    expect(text).not.toMatch(/^design:/m);
    expect(text).not.toContain("null");
    expect(text).not.toContain("checkpoint");
    expect(text).not.toContain("rubric_hash");
  });

  it("reads back as the study it was written from", () => {
    expect(readStudyYaml(toStudyYaml(studyOne))).toEqual(withoutNullKeys(studyOne));
  });
});

describe("toStudyYaml: document", () => {
  it("starts with the two comment lines and ends with one newline, LF only", () => {
    const text = toStudyYaml({ title: "t" });
    expect(text).toBe(`${HEADER}title: "t"\n`);
    expect(toStudyYaml(studyOne)).not.toContain("\r");
    expect(toStudyYaml(studyOne).endsWith("\n")).toBe(true);
    expect(toStudyYaml(studyOne).endsWith("\n\n")).toBe(false);
  });

  it("never leaves trailing whitespace, a tab or a blank line", () => {
    for (const line of toStudyYaml({ ...studyOne, strings: NASTY_STRINGS, deep: { a: [{ b: [[1], {}] }] } }).slice(0, -1).split("\n")) {
      expect(line).not.toBe("");
      expect(line.endsWith(" ")).toBe(false);
      expect(line).not.toContain("\t");
    }
  });

  it("is deterministic and does not touch its input", () => {
    const frozen = deepFreeze(structuredClone(studyOne));
    const first = toStudyYaml(frozen);
    expect(toStudyYaml(frozen)).toBe(first);
    expect(toStudyYaml(structuredClone(studyOne))).toBe(first);
    expect(frozen).toEqual(studyOne);
  });

  it("puts top-level keys in the fixed order, then any others alphabetically", () => {
    const order = ["schema_version", "template", "title", "authors", "contact", "created_at", "models", "arms", "cards", "design", "seeds", "judge", "narrative_model", "supervised", "estimates", "pilot", "expectations", "data_licence"];
    const shuffled = Object.fromEntries([...order, "zeta", "Alpha", "beta", "_first", "qr-extra"].reverse().map((key) => [key, 1]));
    const keys = body(shuffled).trimEnd().split("\n").map((line) => line.slice(0, line.lastIndexOf(":")));
    expect(keys).toEqual([...order, "Alpha", "_first", "beta", '"qr-extra"', "zeta"]);
  });

  it("writes the same bytes whatever order the top-level keys arrive in", () => {
    const reversed = Object.fromEntries(Object.entries(studyOne).reverse());
    expect(toStudyYaml(reversed)).toBe(toStudyYaml(studyOne));
  });

  it("keeps nested keys in the object's own order", () => {
    expect(body({ judge: { zeta: 1, alpha: 2, model: "m" } })).toBe('judge:\n  zeta: 1\n  alpha: 2\n  model: "m"\n');
    expect(body({ arms: [{ source: "qrng", id: "a" }] })).toBe('arms:\n  - source: "qrng"\n    id: "a"\n');
  });

  it("writes an empty study as an empty map", () => {
    expect(body({})).toBe("{}\n");
    expect(body({ design: null, title: undefined })).toBe("{}\n");
    expect(readStudyYaml(toStudyYaml({}))).toEqual({});
  });

  it("refuses anything that is not one study", () => {
    for (const value of [null, undefined, [], [studyOne], "title: x", 3, true, new Date(0), new Map()]) expect(() => toStudyYaml(value)).toThrow(TypeError);
  });

  it("does not validate: a draft with problems is written as it stands", () => {
    const draft = { schema_version: "one", title: 7, arms: "none yet", cards: [{ card_id: 3 }], not_a_setting: [true] };
    expect(body(draft)).toBe('schema_version: "one"\ntitle: 7\narms: "none yet"\ncards:\n  - card_id: 3\nnot_a_setting:\n  - true\n');
  });
});

describe("toStudyYaml: null and undefined", () => {
  it("omits keys whose value is null or undefined, at every depth", () => {
    expect(body({ title: "t", contact: null, judge: { model: "m", temperature: null, rubric_hash: undefined }, arms: [{ id: "a", epsilon: null }] }))
      .toBe('title: "t"\narms:\n  - id: "a"\njudge:\n  model: "m"\n');
  });

  it("writes a map left with no keys as {}", () => {
    expect(body({ cards: [{ card_id: "x", overrides: { k: null, items: null } }] })).toBe('cards:\n  - card_id: "x"\n    overrides: {}\n');
    expect(body({ cards: [{ overrides: { k: null } }] })).toBe("cards:\n  - overrides: {}\n");
    expect(body({ cards: [{ k: null }] })).toBe("cards:\n  - {}\n");
  });

  it("keeps a list's length: an empty slot is written as null", () => {
    expect(body({ seeds: [1, null, 3, undefined] })).toBe("seeds:\n  - 1\n  - null\n  - 3\n  - null\n");
  });
});

describe("toStudyYaml: scalars", () => {
  it("writes every string as a JSON double-quoted scalar", () => {
    expect(body({ title: "plain", contact: "", note: 'say "hi"' })).toBe(`title: "plain"\ncontact: ""\nnote: "say ${BACKSLASH}"hi${BACKSLASH}""\n`);
  });

  it("writes integers as they are", () => {
    expect(body({ seeds: [0, 1, -1, 42, 400.0, 9007199254740991, -0] })).toBe("seeds:\n  - 0\n  - 1\n  - -1\n  - 42\n  - 400\n  - 9007199254740991\n  - 0\n");
  });

  it("writes other numbers as JavaScript prints them", () => {
    expect(body({ values: [0.03, -0.5, 0.1 + 0.2, 1.5e-9] })).toBe("values:\n  - 0.03\n  - -0.5\n  - 0.30000000000000004\n  - 1.5e-9\n");
  });

  it("gives an exponent form a fraction, which YAML 1.1 needs to read it as a number", () => {
    expect(body({ values: [1e21, 1e-7, -2e-9, 5e-324, 1.7976931348623157e308] })).toBe("values:\n  - 1.0e+21\n  - 1.0e-7\n  - -2.0e-9\n  - 5.0e-324\n  - 1.7976931348623157e+308\n");
    for (const value of [1e21, 1e-7, -2e-9, 5e-324, 1.7976931348623157e308, 123456789012345680000]) expect(readStudyYaml(toStudyYaml({ value }))).toEqual({ value });
  });

  it("throws for a number that is not finite, naming where it is", () => {
    expect(() => toStudyYaml({ estimates: { reading_tps: Number.NaN } })).toThrow(/NaN.*estimates\.reading_tps/);
    expect(() => toStudyYaml({ arms: [{ id: "a" }, { epsilon: Number.POSITIVE_INFINITY }] })).toThrow(/Infinity.*arms\[1\]\.epsilon/);
    expect(() => toStudyYaml({ seeds: [1, Number.NEGATIVE_INFINITY] })).toThrow(/-Infinity.*seeds\[1\]/);
    expect(() => toStudyYaml({ estimates: { reading_tps: Number.NaN } })).toThrow(TypeError);
  });

  it("writes booleans as true and false", () => {
    expect(body({ pilot: { enabled: true }, supervised: { group: false } })).toBe("supervised:\n  group: false\npilot:\n  enabled: true\n");
  });

  it("throws for what a study file cannot hold", () => {
    expect(() => toStudyYaml({ created_at: new Date(0) })).toThrow(/non-plain object.*created_at/);
    expect(() => toStudyYaml({ seeds: [10n] })).toThrow(/bigint.*seeds\[0\]/);
    expect(() => toStudyYaml({ judge: { model: () => "m" } })).toThrow(/function.*judge\.model/);
    expect(() => toStudyYaml({ title: Symbol("t") })).toThrow(/symbol.*title/);
  });
});

describe("toStudyYaml: maps", () => {
  it("indents nested maps by two spaces", () => {
    expect(body({ supervised: { settings_by_display: { single: { streams: 1 }, tiled: { streams: 4 } }, group: true } }))
      .toBe("supervised:\n  settings_by_display:\n    single:\n      streams: 1\n    tiled:\n      streams: 4\n  group: true\n");
  });

  it("writes a key bare only when it is a plain identifier", () => {
    const map = Object.fromEntries(NASTY_KEYS.map((key, index) => [key, index]));
    const lines = body({ estimates: { tps_by_model: map } }).trimEnd().split("\n").slice(2);
    expect(lines).toHaveLength(NASTY_KEYS.length);
    lines.forEach((line, index) => {
      const key = NASTY_KEYS[index]!;
      const bare = ["plain_key", "_", "_under", "A1"].includes(key);
      expect(line).toBe(`    ${bare ? key : JSON.stringify(key).replace(c(0x2028), `${BACKSLASH}u2028`)}: ${index}`);
    });
  });

  it("quotes model ids used as keys", () => {
    expect(body({ estimates: { tps_by_model: { "qr-llm": 400, "openai:gpt-5": 80.5, local_model: 12 } } }))
      .toBe('estimates:\n  tps_by_model:\n    "qr-llm": 400\n    "openai:gpt-5": 80.5\n    local_model: 12\n');
  });

  it("quotes the words a YAML 1.1 reader would turn into a boolean or null", () => {
    for (const word of ["y", "Y", "n", "N", "yes", "Yes", "YES", "no", "No", "on", "On", "ON", "off", "OFF", "true", "True", "TRUE", "false", "False", "null", "Null", "NULL"]) {
      expect(body({ flags: { [word]: 1 } })).toBe(`flags:\n  "${word}": 1\n`);
    }
    for (const word of ["yes_please", "nope", "online", "nullable", "truey", "one"]) expect(body({ flags: { [word]: 1 } })).toBe(`flags:\n  ${word}: 1\n`);
  });

  it("reads every awkward key back as the key it was", () => {
    const map = Object.fromEntries(NASTY_KEYS.map((key, index) => [key, { index, nested: [key] }]));
    expect(readStudyYaml(toStudyYaml({ by_key: map }))).toEqual({ by_key: map });
  });
});

describe("toStudyYaml: lists", () => {
  it("writes scalars one per dash", () => {
    expect(body({ authors: ["A", "B"], seeds: [1, 7, 42] })).toBe('authors:\n  - "A"\n  - "B"\nseeds:\n  - 1\n  - 7\n  - 42\n');
  });

  it("puts the first key of a map on the dash line and aligns the rest under it", () => {
    expect(body({ arms: [{ id: "a", model: "qr-llm", deterministic: true }, { id: "b", study_seed: 1 }] }))
      .toBe('arms:\n  - id: "a"\n    model: "qr-llm"\n    deterministic: true\n  - id: "b"\n    study_seed: 1\n');
  });

  it("nests a map or a list under the first key of a list item", () => {
    expect(body({ cards: [{ overrides: { k: 20, items: 30 }, card_id: "x" }, { tags: ["a", "b"], card_id: "y" }] }))
      .toBe('cards:\n  - overrides:\n      k: 20\n      items: 30\n    card_id: "x"\n  - tags:\n      - "a"\n      - "b"\n    card_id: "y"\n');
  });

  it("writes lists of lists, and lists of maps inside them", () => {
    expect(body({ grid: [[1, 2], [], [[3], [4, "five"]], [{ a: 1, b: [2] }, {}]] }))
      .toBe('grid:\n  - - 1\n    - 2\n  - []\n  - - - 3\n    - - 4\n      - "five"\n  - - a: 1\n      b:\n        - 2\n    - {}\n');
  });

  it("writes an empty list as [] and an empty map as {}, wherever they sit", () => {
    expect(body({ authors: [], estimates: {}, arms: [[], {}], judge: { tags: [], extra: {} } }))
      .toBe("authors: []\narms:\n  - []\n  - {}\njudge:\n  tags: []\n  extra: {}\nestimates: {}\n");
  });
});

describe("toStudyYaml: strings that would break a hand-written YAML file", () => {
  const lines = body({ strings: NASTY_STRINGS }).trimEnd().split("\n").slice(1);

  it("keeps each on one line, in JSON quotes, and JSON reads it back unchanged", () => {
    expect(lines).toHaveLength(NASTY_STRINGS.length);
    lines.forEach((line, index) => {
      expect(line.startsWith('  - "')).toBe(true);
      expect(line.endsWith('"')).toBe(true);
      expect(JSON.parse(line.slice(4))).toBe(NASTY_STRINGS[index]);
    });
  });

  it("keeps multi-line strings quoted with \\n and never uses a block scalar", () => {
    expect(body({ expectations: "first\nsecond\n\nfourth\n" })).toBe(`expectations: "first${BACKSLASH}nsecond${BACKSLASH}n${BACKSLASH}nfourth${BACKSLASH}n"\n`);
    expect(body({ expectations: "a\r\nb\tc" })).toBe(`expectations: "a${BACKSLASH}r${BACKSLASH}nb${BACKSLASH}tc"\n`);
    expect(toStudyYaml({ strings: NASTY_STRINGS })).not.toMatch(/: [|>]/);
  });

  it("writes only characters every YAML reader accepts as printable, escaping the rest", () => {
    const printable = (point: number) => point === 0x0a || (point >= 0x20 && point <= 0x7e) || (point >= 0xa0 && point <= 0xd7ff && point !== 0x2028 && point !== 0x2029)
      || (point >= 0xe000 && point <= 0xfffd && point !== 0xfeff) || point >= 0x10000;
    const text = toStudyYaml({ strings: NASTY_STRINGS, by_key: Object.fromEntries(NASTY_KEYS.map((key) => [key, key])) });
    expect([...text].filter((character) => !printable(character.codePointAt(0)!))).toEqual([]);
    const escaped = (codePoint: number) => `  - "${BACKSLASH}u${codePoint.toString(16).padStart(4, "0")}"`;
    for (const codePoint of [0x00, 0x01, 0x1b, 0x7f, 0x80, 0x85, 0x9f, 0x2028, 0x2029, 0xfeff, 0xfffe, 0xffff]) expect(lines).toContain(escaped(codePoint));
    // Ordinary non-ASCII text is written as itself, readable in an editor.
    expect(lines).toContain('  - "Study 1 — quantum"');
    expect(lines).toContain('  - "量子ランダム"');
    expect(lines).toContain(`  - "emoji ${c(0x1f600)} pair"`);
  });

  it("round-trips each of them as a value and as a title", () => {
    expect(readStudyYaml(toStudyYaml({ strings: NASTY_STRINGS }))).toEqual({ strings: NASTY_STRINGS });
    for (const title of NASTY_STRINGS) expect(readStudyYaml(toStudyYaml({ title }))).toEqual({ title });
  });
});

describe("toStudyYaml: generated studies round-trip", () => {
  // Seeded, so a failure is reproducible: the case number is the seed.
  const numbers = [0, 1, -1, 42, 400, 0.03, -0.5, 1e21, 1e-7, 1.5e-9, 9007199254740991, 0.1 + 0.2];
  function generate(random: () => number, depth: number): unknown {
    const pick = <T,>(pool: readonly T[]) => pool[Math.floor(random() * pool.length)]!;
    const roll = random();
    if (depth >= 4 || roll < 0.3) return pick<unknown>([pick(NASTY_STRINGS), pick(numbers), true, false, null, [], {}]);
    const size = Math.floor(random() * 4);
    if (roll < 0.65) return Array.from({ length: size }, () => generate(random, depth + 1));
    return Object.fromEntries(Array.from({ length: size }, () => [pick(NASTY_KEYS), generate(random, depth + 1)]));
  }

  it("for 300 nested mixtures of maps, lists, awkward keys and awkward strings", () => {
    for (let seed = 0; seed < 300; seed++) {
      const random = mulberry32(seed);
      const study = { title: `case ${seed}`, arms: generate(random, 0), cards: generate(random, 0), [NASTY_KEYS[seed % NASTY_KEYS.length]!]: generate(random, 0) };
      const text = toStudyYaml(study);
      expect(toStudyYaml(structuredClone(study)), `case ${seed}`).toBe(text);
      expect(readStudyYaml(text), `case ${seed}`).toEqual(withoutNullKeys(study));
    }
  });
});

describe("studyFileName", () => {
  it("is the title's slug with .study.yaml", () => {
    expect(studyFileName("Study 1 — quantum-random vs seeded pseudo-random token sampling")).toBe("study-1-quantum-random-vs-seeded-pseudo-random-token-sampling.study.yaml");
    expect(studyFileName("  My Study!  ")).toBe("my-study.study.yaml");
    expect(studyFileName("a/b\\c:d*e?f")).toBe("a-b-c-d-e-f.study.yaml");
    expect(studyFileName("../../etc/passwd")).toBe("etc-passwd.study.yaml");
  });

  it("falls back to study when the title has nothing to keep", () => {
    for (const title of ["", "   ", "—", "量子", "!!!"]) expect(studyFileName(title)).toBe("study.study.yaml");
  });

  it("uses the slug rule of preregDownloadName", () => {
    for (const title of ["Study 1 — quantum randomness and supervised observation", "  Mixed CASE & symbols #7 ", "", "ünïcode títle", "a--b__c"]) {
      expect(studyFileName(title).replace(/\.study\.yaml$/, "")).toBe(preregDownloadName(title).replace(/-preregistration\.md$/, ""));
    }
  });
});

describe("fileStateOf", () => {
  const config = studyOne as unknown as WizardStudyConfig;
  const markOf = (value: unknown, name = "study-1.study.yaml"): FileMark => ({ name, snapshot: studySnapshot(value), origin: "loaded" });

  it("is none until a file has been loaded or saved", () => {
    expect(fileStateOf(config, null)).toBe("none");
  });

  it("is saved while the study equals the snapshot, whatever the file is called", () => {
    expect(fileStateOf(config, markOf(config))).toBe("saved");
    expect(fileStateOf(structuredClone(config), markOf(config, "renamed.yaml"))).toBe("saved");
  });

  it("is changed after any edit, and saved again when the edit is undone", () => {
    const mark = markOf(config);
    expect(fileStateOf({ ...config, title: `${config.title}!` }, mark)).toBe("changed");
    expect(fileStateOf({ ...config, seeds: [7, 1, 42] }, mark)).toBe("changed");
    expect(fileStateOf({ ...config, arms: config.arms.slice(1) }, mark)).toBe("changed");
    expect(fileStateOf({ ...config, pilot: { ...config.pilot, prompts: 201 } }, mark)).toBe("changed");
    expect(fileStateOf({ ...config, title: config.title }, mark)).toBe("saved");
  });

  it("does not depend on key order", () => {
    const reordered = Object.fromEntries(Object.entries(config).reverse()) as unknown as WizardStudyConfig;
    expect(fileStateOf(reordered, markOf(config))).toBe("saved");
  });

  it("treats a null setting and a missing one alike, as the file does", () => {
    const sparse = withoutNullKeys(config) as WizardStudyConfig;
    expect(fileStateOf(sparse, markOf(config))).toBe("saved");
    expect(fileStateOf({ ...config, design: null } as WizardStudyConfig, markOf(config))).toBe("saved");
    expect(toStudyYaml(sparse)).toBe(toStudyYaml(config));
  });

  it("is saved exactly when saving again would write the same file", () => {
    const mark = markOf(config);
    const edits: WizardStudyConfig[] = [
      { ...config, expectations: "we expect nothing" },
      { ...config, contact: config.contact },
      { ...config, cards: config.cards.map((card) => ({ ...card })) },
      { ...config, cards: config.cards.map((card, index) => (index === 3 ? { ...card, overrides: { ...card.overrides, k: 5 } } : card)) },
    ];
    for (const edited of edits) expect(fileStateOf(edited, mark) === "saved").toBe(toStudyYaml(edited) === toStudyYaml(config));
  });

  it("is changed against a snapshot it cannot match, never an error", () => {
    expect(fileStateOf(config, { name: "x.yaml", snapshot: "", origin: "loaded" })).toBe("changed");
    expect(fileStateOf(config, { name: "x.yaml", snapshot: "not json", origin: "written" })).toBe("changed");
  });
});

describe("studySnapshot", () => {
  it("is canonical JSON: keys sorted, no whitespace, lists in order (the backend's canonical_json vector)", () => {
    expect(studySnapshot({ b: 1, a: [1.5, "é", null, true], c: { z: 0, y: "x" } })).toBe('{"a":[1.5,"é",null,true],"b":1,"c":{"y":"x","z":0}}');
  });

  it("sorts keys by code unit, not by locale", () => {
    expect(studySnapshot({ b: 1, B: 2, a: 3, _: 4, "é": 5, "10": 6, "9": 7 })).toBe('{"10":6,"9":7,"B":2,"_":4,"a":3,"b":1,"é":5}');
  });

  it("leaves out null and undefined keys and keeps list slots", () => {
    expect(studySnapshot({ a: null, b: undefined, c: [null, undefined, { d: null }], e: {} })).toBe('{"c":[null,null,{}],"e":{}}');
  });

  it("is JSON a parser reads back", () => {
    expect(JSON.parse(studySnapshot(studyOne))).toEqual(withoutNullKeys(studyOne));
  });
});

// ── Saving, loading and the mark of the last file ──

function memoryStorage(): Storage {
  const values = new Map<string, string>();
  return { get length() { return values.size; }, clear: () => values.clear(), getItem: (key) => values.get(key) ?? null, key: (index) => [...values.keys()][index] ?? null, removeItem: (key) => { values.delete(key); }, setItem: (key, value) => { values.set(key, value); } };
}

/** Storage that refuses every call, as a browser with storage blocked does. */
function blockedStorage(): Storage {
  const refuse = () => { throw new DOMException("The operation is insecure.", "SecurityError"); };
  return { get length() { return 0; }, clear: refuse, getItem: refuse, key: refuse, removeItem: refuse, setItem: refuse };
}

/** Stands in for the browser's download, which jsdom does not have: what was handed over, in order. */
function captureDownloads() {
  const blobs: Blob[] = [];
  const names: string[] = [];
  const revoked: string[] = [];
  Object.defineProperty(URL, "createObjectURL", { configurable: true, writable: true, value: (blob: Blob) => { blobs.push(blob); return `blob:study-${blobs.length}`; } });
  Object.defineProperty(URL, "revokeObjectURL", { configurable: true, writable: true, value: (url: string) => { revoked.push(url); } });
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function click(this: HTMLAnchorElement) { names.push(this.download); });
  return { blobs, names, revoked };
}

function blobText(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result ?? ""));
    reader.onerror = () => reject(new Error("The blob could not be read."));
    reader.readAsText(blob);
  });
}

const yamlFile = (text: string, name = "study.study.yaml") => new File([text], name, { type: "application/yaml" });
const failure = (outcome: LoadOutcome) => { if (outcome.ok) throw new Error("The file was loaded."); return outcome; };
const success = (outcome: LoadOutcome) => { if (!outcome.ok) throw new Error(outcome.message); return outcome; };

describe("the mark of the last file", () => {
  it("is kept under qr-evals.study-file.v1 as a name, a snapshot and how the page knows of the file, and read back", () => {
    const storage = memoryStorage();
    expect(FILE_MARK_STORAGE_KEY).toBe("qr-evals.study-file.v1");
    expect(readFileMark(storage)).toBeNull();
    const mark = fileMarkOf("study-1.study.yaml", studyOne);
    expect(mark).toEqual({ name: "study-1.study.yaml", snapshot: studySnapshot(studyOne), origin: "loaded" });
    expect(fileMarkOf("a.yaml", studyOne, "written").origin).toBe("written");
    expect(writeFileMark(mark, storage)).toBe(true);
    expect(JSON.parse(storage.getItem(FILE_MARK_STORAGE_KEY) ?? "null")).toEqual(mark);
    expect(readFileMark(storage)).toEqual(mark);
    expect(fileStateOf(studyOne as unknown as WizardStudyConfig, readFileMark(storage))).toBe("saved");
  });

  it("is forgotten when null is written", () => {
    const storage = memoryStorage();
    writeFileMark(fileMarkOf("a.study.yaml", studyOne), storage);
    expect(writeFileMark(null, storage)).toBe(true);
    expect(storage.getItem(FILE_MARK_STORAGE_KEY)).toBeNull();
    expect(readFileMark(storage)).toBeNull();
  });

  it("reads a mark without an origin, or with one it does not know, as a download: the origin that claims least", () => {
    const storage = memoryStorage();
    for (const stored of ['{"name":"a.yaml","snapshot":"{}"}', '{"name":"a.yaml","snapshot":"{}","origin":"carved in stone"}']) {
      storage.setItem(FILE_MARK_STORAGE_KEY, stored);
      expect(readFileMark(storage)).toEqual({ name: "a.yaml", snapshot: "{}", origin: "downloaded" });
    }
  });

  it("reads anything that is not a mark as no mark, never as an error", () => {
    const storage = memoryStorage();
    for (const stored of ["", "not json", "null", "42", "[]", '{"name":"a.yaml"}', '{"name":1,"snapshot":"{}"}', '{"snapshot":"{}"}']) {
      storage.setItem(FILE_MARK_STORAGE_KEY, stored);
      expect(readFileMark(storage)).toBeNull();
    }
  });

  it("does without storage that is blocked", () => {
    expect(readFileMark(blockedStorage())).toBeNull();
    expect(writeFileMark(fileMarkOf("a.study.yaml", studyOne), blockedStorage())).toBe(false);
    expect(writeFileMark(null, blockedStorage())).toBe(false);
    expect(readFileMark(null)).toBeNull();
    expect(writeFileMark(null, null)).toBe(false);
  });
});

/** Stands in for the browser's "save as" dialog and the file behind it: what was asked for, and what was written and closed. */
function fakePicker(answer: "pick" | "cancel" | "refuse" = "pick", pickedName = "chosen name.study.yaml") {
  const asked: { suggestedName?: string }[] = [];
  const files = new Map<string, { written: string[]; closed: number; failWrite: boolean }>();
  const handleFor = (name: string): StudyFileHandle => {
    const file = files.get(name) ?? { written: [], closed: 0, failWrite: false };
    files.set(name, file);
    return {
      name,
      createWritable: () => Promise.resolve({
        write: (data: string) => { if (file.failWrite) return Promise.reject(new DOMException("The disk is full.", "QuotaExceededError")); file.written.push(data); return Promise.resolve(); },
        close: () => { file.closed += 1; return Promise.resolve(); },
      }),
    };
  };
  const state = { answer, pickedName };
  Object.defineProperty(window, "showSaveFilePicker", {
    configurable: true,
    writable: true,
    value: (options: { suggestedName?: string } = {}) => {
      asked.push(options);
      if (state.answer === "cancel") return Promise.reject(new DOMException("The user aborted a request.", "AbortError"));
      if (state.answer === "refuse") return Promise.reject(new DOMException("Must be handling a user gesture.", "SecurityError"));
      return Promise.resolve(handleFor(state.pickedName));
    },
  });
  return { asked, files, state, handleFor };
}

function removePicker() {
  Reflect.deleteProperty(window, "showSaveFilePicker");
}

describe("saveStudyFile as a download, where the browser has no save dialog", () => {
  beforeEach(() => { removePicker(); Object.defineProperty(window, "localStorage", { configurable: true, value: memoryStorage() }); });
  afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); });

  it("hands the browser a YAML Blob whose text is toStudyYaml(config), under the study's file name", async () => {
    expect(canPickSaveFile()).toBe(false);
    const downloads = captureDownloads();
    const config = studyOneTemplate();
    const saved = await saveStudyFile(config);
    if (saved.status !== "downloaded") throw new Error(`The save was ${saved.status}.`);
    expect(downloads.blobs).toHaveLength(1);
    expect(downloads.blobs[0]!.type).toBe("application/yaml;charset=utf-8");
    expect(await blobText(downloads.blobs[0]!)).toBe(toStudyYaml(config));
    expect(downloads.names).toEqual([studyFileName(config.title)]);
    expect(saved.name).toBe(studyFileName(config.title));
    expect(saved.bytes).toBe(Buffer.byteLength(toStudyYaml(config), "utf8"));
    // The link it clicked is gone again.
    expect(document.querySelector("a[download]")).toBeNull();
  });

  it("records the mark as a download, never as a saved file: the page cannot tell what became of it", async () => {
    captureDownloads();
    const config = studyOneTemplate();
    const saved = await saveStudyFile(config);
    if (saved.status !== "downloaded") throw new Error(`The save was ${saved.status}.`);
    expect(saved.mark).toEqual({ name: saved.name, snapshot: studySnapshot(config), origin: "downloaded" });
    expect(readFileMark()).toEqual(saved.mark);
    expect(fileStateOf(config, readFileMark())).toBe("saved");
    expect(fileStateOf({ ...config, contact: "lab@example.org" }, readFileMark())).toBe("changed");
  });

  it("never validates and never refuses: a draft with problems is saved as it stands", async () => {
    const downloads = captureDownloads();
    const draft = { ...studyOneTemplate(), title: "", arms: [], cards: [] } as WizardStudyConfig;
    expect(parseStudyConfig(draft)).toBeNull();
    const saved = await saveStudyFile(draft);
    expect(saved).toMatchObject({ status: "downloaded", name: "study.study.yaml" });
    expect(await blobText(downloads.blobs[0]!)).toBe(toStudyYaml(draft));
    expect(await blobText(downloads.blobs[0]!)).toContain('title: ""\n');
  });

  it("asks no server", async () => {
    captureDownloads();
    const fetched = vi.spyOn(globalThis, "fetch");
    const parse = vi.spyOn(api, "parseConfig");
    await saveStudyFile(studyOneTemplate());
    expect(fetched).not.toHaveBeenCalled();
    expect(parse).not.toHaveBeenCalled();
  });

  it("lets go of the object URL only after the download has had time to start", () => {
    vi.useFakeTimers();
    const downloads = captureDownloads();
    void saveStudyFile(studyOneTemplate());
    expect(downloads.revoked).toEqual([]);
    vi.advanceTimersByTime(1_000);
    expect(downloads.revoked).toEqual([]);
    vi.advanceTimersByTime(60_000);
    expect(downloads.revoked).toEqual(["blob:study-1"]);
  });

  it("still saves when the mark cannot be stored", async () => {
    Object.defineProperty(window, "localStorage", { configurable: true, value: blockedStorage() });
    const downloads = captureDownloads();
    const config = studyOneTemplate();
    expect(await saveStudyFile(config)).toMatchObject({ status: "downloaded", mark: { snapshot: studySnapshot(config) } });
    expect(await blobText(downloads.blobs[0]!)).toBe(toStudyYaml(config));
  });

  it("rejects with what the writer throws, and records nothing", async () => {
    const downloads = captureDownloads();
    await expect(saveStudyFile({ ...studyOneTemplate(), estimates: { reading_tps: Number.NaN } } as WizardStudyConfig)).rejects.toThrow(/cannot hold NaN/);
    expect(downloads.blobs).toEqual([]);
    expect(readFileMark()).toBeNull();
  });
});

describe("saveStudyFile into a file the researcher picks, where the browser has a save dialog", () => {
  beforeEach(() => { Object.defineProperty(window, "localStorage", { configurable: true, value: memoryStorage() }); });
  afterEach(() => { removePicker(); vi.restoreAllMocks(); });

  it("asks where to save, writes the study, closes the file, and only then records it as saved under the name that was picked", async () => {
    const downloads = captureDownloads();
    const picker = fakePicker();
    expect(canPickSaveFile()).toBe(true);
    const config = studyOneTemplate();
    const saved = await saveStudyFile(config);
    if (saved.status !== "saved") throw new Error(`The save was ${saved.status}.`);
    expect(picker.asked).toHaveLength(1);
    expect(picker.asked[0]!.suggestedName).toBe(studyFileName(config.title));
    expect(picker.files.get("chosen name.study.yaml")).toEqual({ written: [toStudyYaml(config)], closed: 1, failWrite: false });
    expect(saved.name).toBe("chosen name.study.yaml");
    expect(saved.mark).toEqual({ name: "chosen name.study.yaml", snapshot: studySnapshot(config), origin: "written" });
    expect(readFileMark()).toEqual(saved.mark);
    // Nothing went to the downloads beside it.
    expect(downloads.blobs).toEqual([]);
  });

  it("treats a dialog that was closed as nothing happened: no file, no download, no mark", async () => {
    const downloads = captureDownloads();
    const picker = fakePicker("cancel");
    const before = fileMarkOf("earlier.study.yaml", studyOne, "written");
    writeFileMark(before);
    expect(await saveStudyFile(studyOneTemplate())).toEqual({ status: "cancelled" });
    expect(picker.asked).toHaveLength(1);
    expect(picker.files.size).toBe(0);
    expect(downloads.blobs).toEqual([]);
    expect(readFileMark()).toEqual(before);
  });

  it("writes a later save to the same file without asking again", async () => {
    captureDownloads();
    const picker = fakePicker();
    const first = await saveStudyFile(studyOneTemplate());
    if (first.status !== "saved") throw new Error(`The save was ${first.status}.`);
    const edited = { ...studyOneTemplate(), contact: "lab@example.org" };
    const second = await saveStudyFile(edited, { handle: first.handle });
    expect(picker.asked).toHaveLength(1);
    expect(second).toMatchObject({ status: "saved", name: "chosen name.study.yaml", mark: { snapshot: studySnapshot(edited), origin: "written" } });
    expect(picker.files.get("chosen name.study.yaml")!.written).toEqual([toStudyYaml(studyOneTemplate()), toStudyYaml(edited)]);
    expect(picker.files.get("chosen name.study.yaml")!.closed).toBe(2);
  });

  it("asks again without a handle, which is what Save as does", async () => {
    captureDownloads();
    const picker = fakePicker();
    await saveStudyFile(studyOneTemplate());
    picker.state.pickedName = "copy.study.yaml";
    const copy = await saveStudyFile(studyOneTemplate(), { handle: null });
    expect(picker.asked).toHaveLength(2);
    expect(copy).toMatchObject({ status: "saved", name: "copy.study.yaml" });
  });

  it("asks where to save when the file of an earlier save can no longer be written", async () => {
    captureDownloads();
    const picker = fakePicker();
    const gone: StudyFileHandle = { name: "moved.study.yaml", createWritable: () => Promise.reject(new DOMException("The file was not found.", "NotFoundError")) };
    const saved = await saveStudyFile(studyOneTemplate(), { handle: gone });
    expect(picker.asked).toHaveLength(1);
    expect(saved).toMatchObject({ status: "saved", name: "chosen name.study.yaml" });
  });

  it("rejects when the picked file cannot be written, and records nothing", async () => {
    const downloads = captureDownloads();
    const picker = fakePicker();
    picker.handleFor("chosen name.study.yaml");
    picker.files.get("chosen name.study.yaml")!.failWrite = true;
    await expect(saveStudyFile(studyOneTemplate())).rejects.toThrow("The disk is full.");
    expect(picker.files.get("chosen name.study.yaml")!.closed).toBe(0);
    expect(readFileMark()).toBeNull();
    expect(downloads.blobs).toEqual([]);
  });

  it("falls back to a download, marked as one, when the browser refuses to open the dialog", async () => {
    const downloads = captureDownloads();
    fakePicker("refuse");
    const config = studyOneTemplate();
    const saved = await saveStudyFile(config);
    expect(saved).toMatchObject({ status: "downloaded", mark: { origin: "downloaded" } });
    expect(downloads.names).toEqual([studyFileName(config.title)]);
  });
});

describe("loadStudyFile", () => {
  afterEach(() => { vi.restoreAllMocks(); });
  const study = () => ({ ...studyOneTemplate(), title: "Prompt or Physics?" });
  const answer = (config: unknown, unknown_cards: string[] = []) => ({ config, config_hash: "c".repeat(64), unknown_cards }) as Awaited<ReturnType<typeof api.parseConfig>>;

  it("sends the file's text to the server and returns the study the server made of it", async () => {
    const parse = vi.spyOn(api, "parseConfig").mockResolvedValue(answer(study()));
    const outcome = success(await loadStudyFile(yamlFile("title: Prompt or Physics?\n", "thesis.yaml")));
    expect(parse).toHaveBeenCalledExactlyOnceWith({ text: "title: Prompt or Physics?\n" });
    expect(outcome).toEqual({ ok: true, name: "thesis.yaml", config: study(), unknownCards: [], checked: true, problems: [] });
  });

  it("loads a study the server read with problems, and passes the problems on", async () => {
    const problems = ["design.confirmatory names card 'tier0_pit_residuals', which is not selected", "design.claim 'difference_or_equivalence' requires design.sensitivity_epsilon"];
    vi.spyOn(api, "parseConfig").mockResolvedValue({ ...answer(study()), problems } as Awaited<ReturnType<typeof api.parseConfig>>);
    expect(success(await loadStudyFile(yamlFile("x", "draft.study.yaml")))).toEqual({ ok: true, name: "draft.study.yaml", config: study(), unknownCards: [], checked: true, problems });
  });

  it("reads a problem list that is missing or malformed as no problems: an older server sends none", async () => {
    for (const problems of [undefined, null, "one", [1, null, "kept"]]) {
      vi.spyOn(api, "parseConfig").mockResolvedValue({ ...answer(study()), problems } as unknown as Awaited<ReturnType<typeof api.parseConfig>>);
      expect(success(await loadStudyFile(yamlFile("x"))).problems).toEqual(Array.isArray(problems) ? ["kept"] : []);
    }
  });

  it.each([
    ["a seed of 2^53", { seeds: [9007199254740992, 1, 7, 42, 1001] }, "seeds[0]"],
    ["a 63-bit arm seed", { arms: studyOneTemplate().arms.map((arm, index) => (index === 2 ? { ...arm, study_seed: 6148914691236517205 } : arm)) }, "arms[2].study_seed"],
  ])("names the setting of %s the server accepts and a browser cannot keep exactly, and does not blame the page's version", async (_what, patch, setting) => {
    vi.spyOn(api, "parseConfig").mockResolvedValue(answer({ ...study(), ...patch }));
    const outcome = failure(await loadStudyFile(yamlFile("x", "big-seed.yaml")));
    expect(outcome.setting).toBe(setting);
    expect(outcome.message).toBe(`"big-seed.yaml" was not loaded: ${setting} holds a whole number larger than 9007199254740991, the largest this page can keep exactly. Use a smaller number there. Nothing was changed.`);
    expect(outcome.message).not.toContain("out of date");
  });

  it("still says the page is out of date for a study it cannot hold for another reason", async () => {
    vi.spyOn(api, "parseConfig").mockResolvedValue(answer({ ...study(), surprise: 1 }));
    expect(failure(await loadStudyFile(yamlFile("x", "newer.yaml"))).message).toBe('"newer.yaml" was read by the server but this page is out of date. Reload the page and try again.');
  });

  it("passes on the evals the server does not have", async () => {
    vi.spyOn(api, "parseConfig").mockResolvedValue(answer(study(), ["made_up_eval"]));
    expect(success(await loadStudyFile(yamlFile("x"))).unknownCards).toEqual(["made_up_eval"]);
  });

  it("takes one file, a list of one, or any iterable of one", async () => {
    vi.spyOn(api, "parseConfig").mockResolvedValue(answer(study()));
    expect((await loadStudyFile(yamlFile("x"))).ok).toBe(true);
    expect((await loadStudyFile([yamlFile("x")])).ok).toBe(true);
    expect((await loadStudyFile(new Set([yamlFile("x")]))).ok).toBe(true);
  });

  describe("refuses before reading", () => {
    it.each([
      ["no file", [], null, "No file was chosen. Nothing was changed."],
      ["more than one file", [yamlFile("a", "a.yaml"), yamlFile("b", "b.yaml")], null, "A study is one file, and 2 were given. Nothing was changed."],
      ["a file over the server's limit", [yamlFile("x".repeat(MAX_STUDY_FILE_BYTES + 1), "big.yaml")], "big.yaml", '"big.yaml" is larger than a study file can be (200 kB).'],
      ["an empty file", [new File([], "empty.yaml")], "empty.yaml", '"empty.yaml" is empty.'],
    ] as const)("%s", async (_what, files, name, message) => {
      const parse = vi.spyOn(api, "parseConfig");
      const read = vi.spyOn(FileReader.prototype, "readAsText");
      expect(await loadStudyFile([...files])).toEqual({ ok: false, name, message, setting: null });
      expect(read).not.toHaveBeenCalled();
      expect(parse).not.toHaveBeenCalled();
    });

    it("takes a file of exactly the limit", async () => {
      vi.spyOn(api, "parseConfig").mockResolvedValue(answer(study()));
      expect(MAX_STUDY_FILE_BYTES).toBe(200_000);
      expect((await loadStudyFile(yamlFile("x".repeat(MAX_STUDY_FILE_BYTES)))).ok).toBe(true);
    });
  });

  it("does not send a file that holds only whitespace", async () => {
    const parse = vi.spyOn(api, "parseConfig");
    expect(failure(await loadStudyFile(yamlFile(" \n\n", "blank.yaml"))).message).toBe('"blank.yaml" is empty.');
    expect(parse).not.toHaveBeenCalled();
  });

  it("says so when the file cannot be read", async () => {
    const parse = vi.spyOn(api, "parseConfig");
    vi.spyOn(FileReader.prototype, "readAsText").mockImplementation(function fail(this: FileReader) { this.onerror?.(new ProgressEvent("error") as ProgressEvent<FileReader>); });
    expect(failure(await loadStudyFile(yamlFile("x", "locked.yaml"))).message).toBe('"locked.yaml" was not loaded: the file could not be read.');
    expect(parse).not.toHaveBeenCalled();
  });

  it("shows the server's refusal word for word, with the setting it names as a settings path", async () => {
    vi.spyOn(api, "parseConfig").mockRejectedValue(new ApiError("not a valid study configuration: arms.0.sampling: Input should be 'creative' or 't1_minp' (3 problem(s) in all)", 400));
    expect(await loadStudyFile(yamlFile("x", "broken.yaml"))).toEqual({
      ok: false,
      name: "broken.yaml",
      message: "\"broken.yaml\" was not loaded: not a valid study configuration: arms.0.sampling: Input should be 'creative' or 't1_minp' (3 problem(s) in all)",
      setting: "arms[0].sampling",
    });
  });

  it.each([
    ["not a valid study configuration: title: Field required (1 problem(s) in all)", "title"],
    ["not a valid study configuration: design.margins.2.margin: Input should be greater than 0 (1 problem(s) in all)", "design.margins[2].margin"],
    ["not a valid study configuration: cards.11.overrides.k: Input should be a valid integer (2 problem(s) in all)", "cards[11].overrides.k"],
    ["the file is not valid YAML or JSON: mapping values are not allowed here", null],
    ["the file does not hold a study configuration", null],
  ])("names the setting of %j as %j", async (message, setting) => {
    vi.spyOn(api, "parseConfig").mockRejectedValue(new ApiError(message, 400));
    const outcome = failure(await loadStudyFile(yamlFile("x", "f.yaml")));
    expect(outcome.setting).toBe(setting);
    expect(outcome.message).toBe(`"f.yaml" was not loaded: ${message}`);
  });

  it("does not load a study the server accepted but this page cannot hold", async () => {
    vi.spyOn(api, "parseConfig").mockResolvedValue(answer({ ...study(), a_field_this_page_does_not_know: true }));
    expect(await loadStudyFile(yamlFile("x", "newer.yaml"))).toEqual({
      ok: false, name: "newer.yaml", setting: null,
      message: '"newer.yaml" was read by the server but this page is out of date. Reload the page and try again.',
    });
  });

  describe("when the server cannot be reached", () => {
    const unreachable = [
      ["a network error", new TypeError("Failed to fetch")],
      ["a gateway that answers for it", new ApiError("Request failed with status 502", 502)],
      ["a server error", new ApiError("Request failed with status 500", 500)],
    ] as const;

    it.each(unreachable)("loads a complete JSON study without the server's checks (%s)", async (_what, cause) => {
      vi.spyOn(api, "parseConfig").mockRejectedValue(cause);
      const outcome = success(await loadStudyFile(new File([JSON.stringify(study())], "thesis.json", { type: "application/json" })));
      expect(outcome).toEqual({ ok: true, name: "thesis.json", config: study(), unknownCards: [], checked: false, problems: [] });
    });

    // A server that answers with an error was reached. Saying it was not would send the reader to their own connection.
    it.each([
      [unreachable[0], "it could not be reached"],
      [unreachable[1], "it answered with an error (status 502)"],
      [unreachable[2], "it answered with an error (status 500)"],
    ] as const)("does not read a YAML file on its own, and says what the server did (%s)", async ([, cause], why) => {
      vi.spyOn(api, "parseConfig").mockRejectedValue(cause);
      expect(await loadStudyFile(yamlFile(toStudyYaml(study()), "thesis.study.yaml"))).toEqual({
        ok: false, name: "thesis.study.yaml", setting: null,
        message: `"thesis.study.yaml" needs the server to be read, and ${why}. Nothing was changed.`,
      });
    });

    it("says of an incomplete JSON file that the server answered with an error, when it did", async () => {
      vi.spyOn(api, "parseConfig").mockRejectedValue(new ApiError("Request failed with status 503", 503));
      expect(failure(await loadStudyFile(new File([JSON.stringify({ title: "Only a title" })], "partial.json"))).message)
        .toBe('"partial.json" could not be checked because the server answered with an error (status 503), and on its own it is not a complete study. Nothing was changed.');
    });

    it("reads JSON by what the file holds, whatever it is called", async () => {
      vi.spyOn(api, "parseConfig").mockRejectedValue(new TypeError("Failed to fetch"));
      expect(success(await loadStudyFile(new File([JSON.stringify(study())], "thesis.yaml"))).checked).toBe(false);
    });

    it.each([
      ["JSON that is not a complete study", JSON.stringify({ title: "Only a title" }), "partial.json"],
      ["JSON with a setting this page does not know", JSON.stringify({ ...studyOneTemplate(), surprise: 1 }), "newer.json"],
      ["a .json file that is not JSON", "{ title: ", "torn.json"],
      ["a JSON scalar", "42", "number.yaml"],
    ])("does not load %s", async (_what, text, name) => {
      vi.spyOn(api, "parseConfig").mockRejectedValue(new TypeError("Failed to fetch"));
      expect(await loadStudyFile(new File([text], name))).toEqual({
        ok: false, name, setting: null,
        message: `"${name}" could not be checked because the server could not be reached, and on its own it is not a complete study. Nothing was changed.`,
      });
    });
  });

  it("treats a refusal as final: a JSON file the server turned down is not read locally", async () => {
    vi.spyOn(api, "parseConfig").mockRejectedValue(new ApiError("not a valid study configuration: design: Value error, no positive control (1 problem(s) in all)", 400));
    const outcome = failure(await loadStudyFile(new File([JSON.stringify(study())], "thesis.json")));
    expect(outcome.message).toContain('"thesis.json" was not loaded: not a valid study configuration: design: Value error');
    expect(outcome.setting).toBe("design");
  });
});

describe("checkStudyFile: whether the file a save writes will load back", () => {
  afterEach(() => { vi.restoreAllMocks(); });
  const study = (): WizardStudyConfig => ({ ...studyOneTemplate(), contact: "lab@example.org" });
  const answer = (config: unknown) => ({ config, config_hash: "c".repeat(64), unknown_cards: [] }) as Awaited<ReturnType<typeof api.parseConfig>>;
  const offline = () => vi.spyOn(api, "parseConfig").mockRejectedValue(new TypeError("Failed to fetch"));

  it("asks the server to read the very text a save writes, and takes its yes", async () => {
    const parse = vi.spyOn(api, "parseConfig").mockResolvedValue(answer(study()));
    expect(await checkStudyFile(study())).toEqual({ loads: true, by: "server" });
    expect(parse).toHaveBeenCalledExactlyOnceWith({ text: toStudyYaml(study()) }, expect.any(AbortSignal));
  });

  it("takes the server's no, in the server's words, and names the setting", async () => {
    // What the server answers to a study file with an empty title (backend/tests/test_api.py pins the route).
    vi.spyOn(api, "parseConfig").mockRejectedValue(new ApiError("not a valid study configuration: title: String should have at least 1 character (1 problem(s) in all)", 400));
    expect(await checkStudyFile({ ...study(), title: "" })).toEqual({
      loads: false,
      reason: "The server read the same text and refuses it: not a valid study configuration: title: String should have at least 1 character (1 problem(s) in all). The setting it names is title. Fix that in the study and save again.",
    });
  });

  it("does not take a busy or failing server for a no: the page's own check answers", async () => {
    for (const cause of [new ApiError("Too many requests", 429), new ApiError("Request failed with status 502", 502), new TypeError("Failed to fetch")]) {
      vi.spyOn(api, "parseConfig").mockRejectedValue(cause);
      expect(await checkStudyFile(study())).toEqual({ loads: true, by: "page" });
    }
  });

  it.each([
    ["an empty title", (config: WizardStudyConfig) => ({ ...config, title: "" }), "The setting title is empty. The server could not be asked, so this is the page's own check. Fix that and save again."],
    ["no evals", (config: WizardStudyConfig) => ({ ...config, cards: [], design: null }), "The setting cards is empty: it needs at least one entry. The server could not be asked, so this is the page's own check. Fix that and save again."],
    ["no arms", (config: WizardStudyConfig) => ({ ...config, arms: [] }), "The setting arms is empty: it needs at least one entry. The server could not be asked, so this is the page's own check. Fix that and save again."],
    ["an arm that contradicts itself", (config: WizardStudyConfig) => ({ ...config, arms: config.arms.map((arm) => (arm.id === "prng_seed_7" ? { ...arm, study_seed: null } : arm)) }), "Arm prng_seed_7 is a seeded pseudo-random arm and has no seed. Give it one. The server could not be asked, so this is the page's own check. Fix that and save again."],
    ["a setting and an arm", (config: WizardStudyConfig) => ({ ...config, title: "", authors: [], arms: config.arms.map((arm) => (arm.id === "prng_seed_7" ? { ...arm, epsilon: 0.1 } : arm)) }), "The setting title is empty. 2 more things in the study would be refused as well. The server could not be asked, so this is the page's own check. Fix those and save again."],
    ["two settings", (config: WizardStudyConfig) => ({ ...config, title: "", authors: [] }), "The setting title is empty. One more thing in the study would be refused as well. The server could not be asked, so this is the page's own check. Fix those and save again."],
  ])("says without the server that a draft with %s will not load back", async (_what, change, reason) => {
    offline();
    expect(await checkStudyFile(change(study()))).toEqual({ loads: false, reason });
  });

  it("says no when the server accepts the text and this page could not hold what it reads from it", async () => {
    vi.spyOn(api, "parseConfig").mockResolvedValue(answer({ ...study(), seeds: [2 ** 60] }));
    const large = await checkStudyFile(study());
    expect(large).toEqual({ loads: false, reason: `seeds[0] holds a whole number larger than ${Number.MAX_SAFE_INTEGER}, the largest this page can keep exactly. Use a smaller number there and save again.` });
    vi.spyOn(api, "parseConfig").mockResolvedValue(answer({ ...study(), from_a_newer_server: true }));
    expect(await checkStudyFile(study())).toEqual({ loads: false, reason: "The server accepts the text, but this page is out of date and cannot hold what the server reads from it. Reload the page and save again." });
  });

  it("does not wait on a server that does not answer", async () => {
    // The request is given up after the time allowed, and the page answers.
    const parse = vi.spyOn(api, "parseConfig").mockImplementation((_body, signal) => new Promise((_resolve, reject) => {
      signal?.addEventListener("abort", () => reject(new DOMException("The operation was aborted.", "AbortError")));
    }));
    expect(await checkStudyFile(study(), { timeoutMs: 5 })).toEqual({ loads: true, by: "page" });
    expect(parse).toHaveBeenCalledTimes(1);
  });

  it("never rejects, also for a study no file can hold", async () => {
    const parse = offline();
    const check = await checkStudyFile({ ...study(), estimates: { tokens_per_second: Number.NaN } });
    expect(check.loads).toBe(false);
    expect(parse).not.toHaveBeenCalled();
  });
});

describe("the mark of a file that will not load back", () => {
  it("keeps that across a visit, and says nothing of a file that was not found wanting", () => {
    const storage = memoryStorage();
    const mark: FileMark = { ...fileMarkOf("draft.study.yaml", { title: "" }, "written"), loadsBack: false };
    expect(writeFileMark(mark, storage)).toBe(true);
    expect(readFileMark(storage)).toEqual(mark);
    writeFileMark(fileMarkOf("fine.study.yaml", { title: "x" }, "written"), storage);
    expect(readFileMark(storage)).not.toHaveProperty("loadsBack");
    // Only `false` is ever kept: anything else in its place is not a claim that the file loads.
    storage.setItem(FILE_MARK_STORAGE_KEY, '{"name":"a.yaml","snapshot":"{}","origin":"written","loadsBack":"no"}');
    expect(readFileMark(storage)).toEqual({ name: "a.yaml", snapshot: "{}", origin: "written" });
  });
});

describe("a saved file loads back as the study it was saved from", () => {
  beforeEach(() => { Object.defineProperty(window, "localStorage", { configurable: true, value: memoryStorage() }); });
  afterEach(() => { vi.restoreAllMocks(); });

  it("gives the server the saved bytes, and the loaded study has the saved study's mark", async () => {
    const downloads = captureDownloads();
    const config = { ...studyOneTemplate(), contact: "lab@example.org" };
    const saved = await saveStudyFile(config);
    if (saved.status !== "downloaded") throw new Error(`The save was ${saved.status}.`);
    const text = await blobText(downloads.blobs[0]!);

    // The server's side of the round trip is pinned in backend/tests/test_study_file_roundtrip.py; here it answers with the study it was sent.
    const parse = vi.spyOn(api, "parseConfig").mockResolvedValue({ config, config_hash: "c".repeat(64), unknown_cards: [] });
    const outcome = success(await loadStudyFile(new File([downloads.blobs[0]!], saved.name)));
    expect(parse).toHaveBeenCalledExactlyOnceWith({ text });
    expect(text).toBe(toStudyYaml(config));
    expect(outcome.config).toEqual(config);
    expect(fileMarkOf(outcome.name, outcome.config, "downloaded")).toEqual(saved.mark);
    expect(fileStateOf(outcome.config, saved.mark)).toBe("saved");
  });
});
