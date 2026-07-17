function _flattenErrors(obj: unknown): string {
  if (typeof obj === "string") return obj;
  if (Array.isArray(obj)) return obj.map(_flattenErrors).filter(Boolean).join(" | ");
  if (obj && typeof obj === "object") {
    return Object.values(obj as Record<string, unknown>)
      .map(_flattenErrors)
      .filter(Boolean)
      .join(" | ");
  }
  return "";
}

function _parseErrorBody(body: string): string {
  try {
    const parsed = JSON.parse(body);
    return _flattenErrors(parsed) || body;
  } catch {
    return body;
  }
}

export async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const token = typeof window !== "undefined" ? localStorage.getItem("token") : null;
  const res = await fetch(path, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...options.headers,
    },
  });
  if (res.status === 401) {
    localStorage.removeItem("token");
    window.location.href = "/login";
    throw new Error("פג תוקף ההתחברות, מועבר לדף הכניסה");
  }
  if (!res.ok) {
    const body = await res.text();
    throw new Error(_parseErrorBody(body) || res.statusText);
  }
  if (res.status === 204) return null as T;
  return res.json();
}

// ─────────────── Auth ───────────────

export interface TokenPair {
  access: string;
  refresh: string;
}

export async function login(email: string, password: string): Promise<TokenPair> {
  return request<TokenPair>("/api/users/login/", {
    method: "POST",
    body: JSON.stringify({ email, password }),
  });
}

// ─────────────── User / Me ───────────────

export interface UserProfile {
  company_name: string;
  company_phone: string;
  company_address: string;
  phone: string;
  position: string;
  region: string;
}

export interface Me {
  id: number;
  email: string;
  first_name: string;
  last_name: string;
  is_staff: boolean;
  profile: UserProfile | null;
}

export async function fetchMe(): Promise<Me> {
  return request<Me>("/api/users/me/");
}

export interface AdminUser {
  id: number;
  email: string;
  first_name: string;
  last_name: string;
  profile: UserProfile | null;
}

export interface CreateUserPayload {
  email: string;
  password: string;
  password2: string;
  first_name: string;
  last_name: string;
  company_name: string;
  company_phone: string;
  company_address: string;
  phone: string;
  position: string;
  region: string;
}

export async function fetchAdminUsers(): Promise<AdminUser[]> {
  return request<AdminUser[]>("/api/users/admin/users/");
}

export async function createUser(data: CreateUserPayload): Promise<AdminUser> {
  return request<AdminUser>("/api/users/register/", {
    method: "POST",
    body: JSON.stringify(data),
  });
}

export async function deleteUser(id: number): Promise<void> {
  return request<void>(`/api/users/admin/users/${id}/`, { method: "DELETE" });
}

export async function updateAdminUserProfile(id: number, data: Partial<UserProfile>): Promise<UserProfile> {
  return request<UserProfile>(`/api/users/admin/users/${id}/profile/`, {
    method: "PATCH",
    body: JSON.stringify(data),
  });
}

// ─────────────── Orders ───────────────

export interface OrderSummary {
  id: number;
  status: string;
  total_price: string;
  created_at: string;
  product_count: number;
}

export async function fetchOrders(): Promise<OrderSummary[]> {
  return request<OrderSummary[]>("/api/orders/");
}

export interface SupplierSpending {
  supplier_id: number;
  supplier_name: string;
  total_spent: string;
  order_count: number;
}

export interface OrderStats {
  total_spent: string;
  order_count: number;
  by_supplier: SupplierSpending[];
}

export async function fetchStats(): Promise<OrderStats> {
  return request<OrderStats>("/api/orders/stats/");
}

export interface OrderItemDetail {
  product_id: number;
  product_name: string;
  supplier_id: number;
  supplier_name: string;
  quantity: string;
  unit_display: string;
  unit_price: string;
  subtotal: string;
}

export interface OrderDetail {
  id: number;
  status: string;
  total_price: string;
  created_at: string;
  products: OrderItemDetail[];
}

export async function fetchOrderDetail(id: number): Promise<OrderDetail> {
  return request<OrderDetail>(`/api/orders/${id}/`);
}

export async function updateOrderStatus(id: number, status: string): Promise<void> {
  return request<void>(`/api/orders/${id}/status/`, {
    method: "PATCH",
    body: JSON.stringify({ status }),
  });
}

// ─────────────── Suggest & Place order ───────────────

export interface OrderProductInput {
  product_name: string;
  quantity: string;
}

export interface SuggestProduct {
  product_id: number;
  product_name: string;
  unit: string;
  quantity: string;
  unit_price: string;
  subtotal: string;
  supplier_id: number;
  supplier_name: string;
}

export interface SuggestScenario {
  scenario: string;
  total_price: string;
  supplier_count: number;
  products: SuggestProduct[];
}

export interface MinimumIssue {
  supplier_id: number;
  supplier_name: string;
  current_total: string;
  minimum_required: string;
  missing_amount: string;
}

export interface SuggestOrderResponse {
  cheapest: SuggestScenario;
  fewest_suppliers: SuggestScenario;
  market_comparison: {
    products: unknown[];
    our_total: string;
    market_total: string | null;
    total_savings: string | null;
  };
  minimum_issues: {
    cheapest: MinimumIssue[];
    fewest_suppliers: MinimumIssue[];
  };
}

export async function suggestOrder(
  products: OrderProductInput[]
): Promise<SuggestOrderResponse> {
  return request<SuggestOrderResponse>("/api/orders/suggest/", {
    method: "POST",
    body: JSON.stringify({ products }),
  });
}

export interface WhatsAppLink {
  supplier_id: number;
  supplier_name: string;
  phone: string;
  whatsapp_url: string;
}

export interface PlaceOrderResponse {
  order_id: number;
  status: string;
  total_price: string;
  scenario: string;
  whatsapp_links: WhatsAppLink[];
}

export async function placeOrder(
  products: OrderProductInput[],
  scenario: "cheapest" | "fewest_suppliers",
  region: string
): Promise<PlaceOrderResponse> {
  return request<PlaceOrderResponse>("/api/orders/place/", {
    method: "POST",
    body: JSON.stringify({ products, scenario, region }),
  });
}

// ─────────────── Shopping Lists ───────────────

export interface ShoppingListItem {
  id?: number;
  product_name: string;
  default_quantity: string;
}

export interface ShoppingList {
  id: number;
  name: string;
  is_primary: boolean;
  created_at: string;
  products: ShoppingListItem[];
}

export async function fetchShoppingLists(): Promise<ShoppingList[]> {
  return request<ShoppingList[]>("/api/orders/shopping-lists/");
}

export async function fetchShoppingList(id: number): Promise<ShoppingList> {
  return request<ShoppingList>(`/api/orders/shopping-lists/${id}/`);
}

export async function createShoppingList(data: {
  name: string;
  is_primary?: boolean;
  products: { product_name: string; default_quantity: string }[];
}): Promise<ShoppingList> {
  return request<ShoppingList>("/api/orders/shopping-lists/", {
    method: "POST",
    body: JSON.stringify(data),
  });
}

export async function updateShoppingList(
  id: number,
  data: {
    name: string;
    is_primary?: boolean;
    products: { product_name: string; default_quantity: string }[];
  }
): Promise<ShoppingList> {
  return request<ShoppingList>(`/api/orders/shopping-lists/${id}/`, {
    method: "PUT",
    body: JSON.stringify(data),
  });
}

export async function deleteShoppingList(id: number): Promise<void> {
  return request<void>(`/api/orders/shopping-lists/${id}/`, { method: "DELETE" });
}

export async function suggestFromShoppingList(
  id: number,
  region: string
): Promise<SuggestOrderResponse> {
  return request<SuggestOrderResponse>(`/api/orders/shopping-lists/${id}/suggest/`, {
    method: "POST",
    body: JSON.stringify({ region }),
  });
}

// ─────────────── Catalog ───────────────

export interface Product {
  id: number;
  name: string;
  unit: string;
  unit_display: string;
}

export async function fetchProducts(): Promise<Product[]> {
  return request<Product[]>("/api/catalog/products/");
}

export interface CatalogSupplierPrice {
  id: number;
  name: string;
  region: string;
  price: string;
  updated_at: string;
  is_cheapest: boolean;
}

export interface CatalogProduct {
  product_id: number;
  product_name: string;
  unit: string;
  unit_display: string;
  market_price: string | null;
  market_grade_a: string | null;
  market_premium: string | null;
  market_date: string | null;
  cheapest_price: string | null;
  cheapest_supplier_name: string | null;
  suppliers: CatalogSupplierPrice[];
}

export async function fetchProductCatalog(search?: string): Promise<CatalogProduct[]> {
  const qs = search ? `?search=${encodeURIComponent(search)}` : "";
  return request<CatalogProduct[]>(`/api/catalog/product-prices/${qs}`);
}

// ─────────────── Suppliers ───────────────

export interface SupplierProduct {
  product_id: number;
  product_name: string;
  unit: string;
  price_per_unit: string;
  updated_at: string;
}

export interface SupplierWithProducts {
  id: number;
  name: string;
  phone: string;
  whatsapp_number: string;
  region: string;
  minimum_order: string;
  products: SupplierProduct[];
}

export async function fetchSuppliersAll(): Promise<SupplierWithProducts[]> {
  return request<SupplierWithProducts[]>("/api/catalog/suppliers/prices/all/");
}

export interface CreateSupplierPayload {
  name: string;
  phone: string;
  whatsapp_number: string;
  region: string;
  minimum_order: string;
}

export async function createSupplier(data: CreateSupplierPayload): Promise<SupplierWithProducts> {
  return request<SupplierWithProducts>("/api/catalog/suppliers/", {
    method: "POST",
    body: JSON.stringify(data),
  });
}

export async function deleteSupplier(id: number): Promise<void> {
  return request<void>(`/api/catalog/suppliers/${id}/`, { method: "DELETE" });
}

export async function updateSupplier(id: number, data: Partial<CreateSupplierPayload>): Promise<SupplierWithProducts> {
  return request<SupplierWithProducts>(`/api/catalog/suppliers/${id}/`, {
    method: "PATCH",
    body: JSON.stringify(data),
  });
}

export interface ProfilePayload {
  company_name?: string;
  company_phone?: string;
  company_address?: string;
  phone?: string;
  position?: string;
  region?: string;
}

export async function updateProfile(data: ProfilePayload): Promise<ProfilePayload> {
  return request<ProfilePayload>("/api/users/me/profile/", {
    method: "PATCH",
    body: JSON.stringify(data),
  });
}

export async function deleteProduct(id: number): Promise<void> {
  return request<void>(`/api/catalog/products/${id}/`, { method: "DELETE" });
}

export interface CreateProductPayload {
  name: string;
  unit: string;
}

export async function createProduct(data: CreateProductPayload): Promise<Product> {
  return request<Product>("/api/catalog/products/", {
    method: "POST",
    body: JSON.stringify(data),
  });
}
