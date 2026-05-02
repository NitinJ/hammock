<template>
  <div class="flex min-h-screen bg-surface">
    <SideNav />
    <div class="flex flex-1 flex-col">
      <TopBar />
      <main class="flex-1 overflow-auto p-6">
        <RouterView />
      </main>
    </div>
  </div>
</template>

<script setup lang="ts">
import { RouterView } from "vue-router";
import SideNav from "@/components/nav/SideNav.vue";
import TopBar from "@/components/nav/TopBar.vue";
import { useGlobalStore } from "@/stores/global";
import { useEventStream } from "@/sse";

const globalStore = useGlobalStore();

useEventStream("global", {
  onEvent: (event) => globalStore.applyEvent(event),
  onConnect: () => globalStore.setConnected(true),
  onDisconnect: () => globalStore.setConnected(false),
});
</script>
