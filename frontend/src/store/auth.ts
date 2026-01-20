import { create } from "zustand";

type AuthState = {
  user: any | null;
  accessToken: string | null;
  setAuth: (user: any, access: string | null) => void;
  clear: () => void;
};

export const useAuthStore = create<AuthState>((set) => ({
  user: null,
  accessToken: null,
  setAuth: (user, access) => set({ user, accessToken: access }),
  clear: () => set({ user: null, accessToken: null }),
}));
