import { defineStore } from "pinia";
import { ref } from "vue";
import type { HilItem } from "@/api/schema.d";

export const useHilStore = defineStore("hil", () => {
  const awaitingItems = ref<HilItem[]>([]);

  function setItems(list: HilItem[]): void {
    awaitingItems.value = list;
  }

  function addItem(item: HilItem): void {
    if (!awaitingItems.value.find((i) => i.item_id === item.item_id)) {
      awaitingItems.value.push(item);
    }
  }

  function removeItem(itemId: string): void {
    awaitingItems.value = awaitingItems.value.filter((i) => i.item_id !== itemId);
  }

  return { awaitingItems, setItems, addItem, removeItem };
});
