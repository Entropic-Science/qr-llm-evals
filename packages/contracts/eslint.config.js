// @ts-check
import tseslint from "typescript-eslint";

export default tseslint.config(
  // src/generated is machine-written by scripts/export_schemas.py; lint the source, not the output.
  { ignores: ["dist/**", "node_modules/**", "src/generated/**"] },
  ...tseslint.configs.recommended,
);
