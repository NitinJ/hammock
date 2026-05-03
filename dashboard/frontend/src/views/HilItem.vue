<template>
  <div class="hil-item-view">
    <div v-if="loading" class="loading">Loading…</div>
    <div v-else-if="fetchError" class="error" role="alert">{{ fetchError }}</div>
    <div v-else-if="detail" class="hil-form-container">
      <h1>{{ pageTitle }}</h1>
      <p v-if="detail.item.status !== 'awaiting'" class="already-answered">
        This item is already {{ detail.item.status }}.
      </p>
      <FormRenderer
        v-else
        :item="detail"
        :template="template"
        :submitting="submitting"
        :error="submitError"
        @submit="handleSubmit"
      />
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useRoute } from "vue-router";
import FormRenderer from "@/components/forms/FormRenderer.vue";
import { fetchTemplate } from "@/components/forms/TemplateRegistry";
import type { UiTemplate } from "@/components/forms/TemplateRegistry";

interface HilItemDetail {
  item: { id: string; kind: string; status: string; question: unknown; [k: string]: unknown };
  job_slug: string | null;
  project_slug: string | null;
  ui_template_name: string;
}

const route = useRoute();
const itemId = computed(() => route.params["itemId"] as string);

const detail = ref<HilItemDetail | null>(null);
const template = ref<UiTemplate | null>(null);
const loading = ref(true);
const fetchError = ref<string | null>(null);
const submitting = ref(false);
const submitError = ref<string | null>(null);

const pageTitle = computed(() => {
  if (!detail.value) return "HIL Item";
  const kind = detail.value.item.kind;
  return kind === "ask"
    ? "Answer Required"
    : kind === "review"
      ? "Review Required"
      : "Manual Step Required";
});

onMounted(async () => {
  await loadItem();
});

async function loadItem() {
  loading.value = true;
  fetchError.value = null;
  try {
    const res = await fetch(`/api/hil/${itemId.value}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    detail.value = (await res.json()) as HilItemDetail;
    const tpl = await fetchTemplate(
      detail.value.ui_template_name,
      detail.value.project_slug ?? undefined,
    );
    if (tpl === null) {
      throw new Error(`Template '${detail.value.ui_template_name}' not found`);
    }
    template.value = tpl;
  } catch (e) {
    fetchError.value = e instanceof Error ? e.message : "Failed to load item";
  } finally {
    loading.value = false;
  }
}

async function handleSubmit(answer: unknown) {
  if (!detail.value) return;
  submitting.value = true;
  submitError.value = null;
  try {
    const res = await fetch(`/api/hil/${itemId.value}/answer`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(answer),
    });
    if (!res.ok) {
      const body = (await res.json().catch(() => ({}))) as { detail?: string };
      throw new Error(body.detail ?? `HTTP ${res.status}`);
    }
    // Reload to show answered state
    await loadItem();
  } catch (e) {
    submitError.value = e instanceof Error ? e.message : "Submit failed";
  } finally {
    submitting.value = false;
  }
}
</script>
