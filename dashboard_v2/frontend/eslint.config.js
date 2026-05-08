import vue from "eslint-plugin-vue";
import vueTsConfig from "@vue/eslint-config-typescript";
import vuePrettierConfig from "@vue/eslint-config-prettier";

export default [
  ...vue.configs["flat/recommended"],
  ...vueTsConfig(),
  vuePrettierConfig,
  {
    rules: {
      "vue/multi-word-component-names": "off",
      "vue/html-self-closing": "off",
      "vue/max-attributes-per-line": "off",
      "vue/singleline-html-element-content-newline": "off",
      "vue/html-closing-bracket-newline": "off",
      "vue/html-indent": "off",
      "vue/attributes-order": "off",
      "vue/first-attribute-linebreak": "off",
      "@typescript-eslint/no-unused-vars": [
        "error",
        { argsIgnorePattern: "^_", varsIgnorePattern: "^_" },
      ],
    },
  },
  {
    ignores: ["dist", "node_modules", "*.config.js", "*.config.ts"],
  },
];
