import { config } from "@vue/test-utils";
import { createPinia } from "pinia";

// Global Pinia for all component tests
config.global.plugins = [createPinia()];
