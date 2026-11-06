import { apiRequest } from "./client";
import { AnalisisGlobal } from "@/src/types/analisis.types";

export async function getAnalisisGlobal(): Promise<AnalisisGlobal> {
  return apiRequest<AnalisisGlobal>("/analisis", { auth: true });
}
