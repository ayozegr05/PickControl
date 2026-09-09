// Equivalente TS de backend/app/schemas/user.py

export type UserRole = "user" | "admin";

export interface User {
  id: number;
  name: string;
  email: string;
  role: UserRole;
  isActive: boolean;
  createdAt: string; // ISO 8601
  lastLogin?: string | null;
}

export interface LoginPayload {
  email: string;
  password: string;
}

export interface RegisterPayload extends LoginPayload {
  name: string;
}

export interface AuthResponse {
  message: string;
  token: string;
  user: Pick<User, "id" | "name" | "email" | "role">;
}
