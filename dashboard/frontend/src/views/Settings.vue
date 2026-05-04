<template>
  <div class="p-6 space-y-8">
    <h1 class="text-2xl font-bold text-text-primary">Settings</h1>

    <!-- System health + runner mode -->
    <section class="bg-surface border border-border rounded-lg p-4 space-y-3">
      <h2 class="text-lg font-semibold text-text-primary">System</h2>
      <div v-if="isPending" class="text-text-secondary text-sm">Loading…</div>
      <div v-else-if="isError" class="text-red-400 text-sm">Failed to load settings.</div>
      <template v-else-if="data">
        <div class="grid grid-cols-3 gap-4 text-sm">
          <div>
            <p class="text-text-secondary">Runner mode</p>
            <p
              class="font-semibold"
              :class="data.runner_mode === 'real' ? 'text-yellow-400' : 'text-green-400'"
            >
              {{ data.runner_mode }}
            </p>
          </div>
          <div>
            <p class="text-text-secondary">Claude binary</p>
            <p class="font-mono text-xs text-text-primary">
              {{ data.claude_binary ?? "—" }}
            </p>
          </div>
          <div>
            <p class="text-text-secondary">Cache entries</p>
            <p class="font-semibold text-text-primary">{{ data.cache_size }}</p>
          </div>
          <div>
            <p class="text-text-secondary">MCP servers</p>
            <p class="font-semibold text-text-primary">{{ data.mcp_server_count }}</p>
          </div>
        </div>
      </template>
    </section>

    <!-- Active jobs -->
    <section
      v-if="data && data.active_jobs.length > 0"
      class="bg-surface border border-border rounded-lg p-4 space-y-3"
    >
      <h2 class="text-lg font-semibold text-text-primary">
        Active jobs ({{ data.active_jobs.length }})
      </h2>
      <table class="w-full text-sm">
        <thead class="text-text-secondary">
          <tr>
            <th class="text-left">Job</th>
            <th class="text-left">State</th>
            <th class="text-left">Heartbeat age</th>
            <th class="text-left">Driver PID</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="job in data.active_jobs" :key="job.job_slug" class="border-t border-border">
            <td class="font-mono text-xs py-1">{{ job.job_slug }}</td>
            <td class="py-1">{{ job.state }}</td>
            <td class="py-1">
              {{
                job.heartbeat_age_seconds == null
                  ? "—"
                  : `${Math.round(job.heartbeat_age_seconds)}s`
              }}
            </td>
            <td class="py-1">
              <span v-if="job.pid == null" class="text-text-secondary">—</span>
              <span v-else :class="job.pid_alive ? 'text-green-400' : 'text-red-400'">
                {{ job.pid }} {{ job.pid_alive ? "alive" : "dead" }}
              </span>
            </td>
          </tr>
        </tbody>
      </table>
    </section>

    <!-- Projects + doctor status + per-project inventory -->
    <section
      v-if="data && data.projects.length > 0"
      class="bg-surface border border-border rounded-lg p-4 space-y-3"
    >
      <h2 class="text-lg font-semibold text-text-primary">
        Projects ({{ data.projects.length }})
      </h2>
      <table class="w-full text-sm">
        <thead class="text-text-secondary">
          <tr>
            <th class="text-left">Slug</th>
            <th class="text-left">Doctor</th>
            <th class="text-left">Last check</th>
            <th class="text-left">Agent overrides</th>
            <th class="text-left">Skill overrides</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="p in data.projects" :key="p.slug" class="border-t border-border">
            <td class="font-mono text-xs py-1">{{ p.slug }}</td>
            <td class="py-1">
              <span :class="doctorClass(p.doctor_status)">
                {{ p.doctor_status ?? "—" }}
              </span>
            </td>
            <td class="py-1">
              {{ p.last_health_check_at ?? "—" }}
            </td>
            <td class="py-1">{{ data.inventory.agents_per_project[p.slug] ?? 0 }}</td>
            <td class="py-1">{{ data.inventory.skills_per_project[p.slug] ?? 0 }}</td>
          </tr>
        </tbody>
      </table>
      <p class="text-text-secondary text-xs">
        Total overrides: {{ data.inventory.total_agent_overrides }} agents,
        {{ data.inventory.total_skill_overrides }} skills
      </p>
    </section>

    <!-- About -->
    <section class="bg-surface border border-border rounded-lg p-4">
      <h2 class="text-lg font-semibold text-text-primary mb-3">About</h2>
      <p class="text-text-secondary text-sm">
        Hammock Dashboard — v0. See
        <a class="underline" href="https://github.com/NitinJ/hammock">NitinJ/hammock</a>.
      </p>
    </section>
  </div>
</template>

<script setup lang="ts">
import { useQuery } from "@tanstack/vue-query";

interface ActiveJob {
  job_slug: string;
  state: string;
  heartbeat_age_seconds: number | null;
  pid: number | null;
  pid_alive: boolean;
}

interface ProjectStatus {
  slug: string;
  doctor_status: string | null;
  last_health_check_at: string | null;
}

interface Inventory {
  agents_per_project: Record<string, number>;
  skills_per_project: Record<string, number>;
  total_agent_overrides: number;
  total_skill_overrides: number;
}

interface SettingsResponse {
  runner_mode: string;
  claude_binary: string | null;
  cache_size: number;
  active_jobs: ActiveJob[];
  projects: ProjectStatus[];
  inventory: Inventory;
  mcp_server_count: number;
}

const { data, isPending, isError } = useQuery<SettingsResponse>({
  queryKey: ["settings"],
  queryFn: async () => {
    const res = await fetch("/api/settings");
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return (await res.json()) as SettingsResponse;
  },
  refetchInterval: 5_000,
});

function doctorClass(status: string | null) {
  if (status === "pass") return "text-green-400";
  if (status === "warn") return "text-yellow-400";
  if (status === "fail") return "text-red-400";
  return "text-text-secondary";
}
</script>
