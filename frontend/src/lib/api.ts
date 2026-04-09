import axios from "axios";
import { getToken, clearToken } from "./auth";

const api = axios.create({
  baseURL: process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000",
});

// Request interceptor: attach JWT token
api.interceptors.request.use((config) => {
  const token = getToken();
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

// Response interceptor: redirect on 401
api.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401) {
      clearToken();
      if (typeof window !== "undefined") {
        window.location.href = "/login";
      }
    }
    return Promise.reject(error);
  }
);

// ─── Auth ────────────────────────────────────────────────────────────────────

export async function register(
  email: string,
  password: string,
  tenantName: string
) {
  const res = await api.post("/auth/register", {
    email,
    password,
    tenant_name: tenantName,
  });
  return res.data;
}

export async function login(email: string, password: string) {
  const res = await api.post("/auth/login", { email, password });
  return res.data;
}

// ─── Stores ──────────────────────────────────────────────────────────────────

export interface StorePayload {
  name: string;
  marketplace: string;
  seller_id?: string;
  [key: string]: unknown;
}

export async function getStores() {
  const res = await api.get("/stores");
  return res.data;
}

export async function createStore(data: StorePayload) {
  const res = await api.post("/stores", data);
  return res.data;
}

export async function updateStore(id: number, data: Partial<StorePayload>) {
  const res = await api.put(`/stores/${id}`, data);
  return res.data;
}

export async function deleteStore(id: number) {
  const res = await api.delete(`/stores/${id}`);
  return res.data;
}

// ─── Products ────────────────────────────────────────────────────────────────

export interface ProductPayload {
  sku: string;
  name: string;
  store_id: number;
  cogs_rmb?: number;
  freight_rmb_per_unit?: number;
  duty_rate_pct?: number;
  [key: string]: unknown;
}

export async function getProducts() {
  const res = await api.get("/products");
  return res.data;
}

export async function createProduct(data: ProductPayload) {
  const res = await api.post("/products", data);
  return res.data;
}

export async function importProductsCsv(file: File) {
  const formData = new FormData();
  formData.append("file", file);
  const res = await api.post("/products/import-csv", formData, {
    headers: { "Content-Type": "multipart/form-data" },
  });
  return res.data;
}

// ─── Sales ───────────────────────────────────────────────────────────────────

export async function importSalesCsv(file: File) {
  const formData = new FormData();
  formData.append("file", file);
  const res = await api.post("/sales/import-csv", formData, {
    headers: { "Content-Type": "multipart/form-data" },
  });
  return res.data;
}

// ─── Profit ──────────────────────────────────────────────────────────────────

export interface ProfitParams {
  start_date?: string;
  end_date?: string;
  store_id?: number;
  category?: string;
  [key: string]: unknown;
}

export async function getProfitBySku(params?: ProfitParams) {
  const res = await api.get("/profit/by-sku", { params });
  return res.data;
}

export async function getProfitSummary(params?: ProfitParams) {
  const res = await api.get("/profit/summary", { params });
  return res.data;
}

export async function getUnprofitable(params?: ProfitParams) {
  const res = await api.get("/profit/unprofitable", { params });
  return res.data;
}

// ─── Sync ────────────────────────────────────────────────────────────────────

export async function syncFx() {
  const res = await api.post("/sync/fx");
  return res.data;
}

export default api;
