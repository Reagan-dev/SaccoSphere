import { create } from "zustand";

type User = any; // tighten later

type AuthState = {
  user: User | null;
  accessToken: string | null;
  initialized: boolean;

  setUser: (user: User | null) => void;
  setAccessToken: (token: string | null) => void;
  setInitialized: (value: boolean) => void;
  clear: () => void;
};

export const useAuthStore = create<AuthState>((set) => ({
  user: null,
  accessToken: null,
  initialized: false,

  setUser: (user) => set({ user }),
  setAccessToken: (accessToken) => set({ accessToken }),
  setInitialized: (initialized) => set({ initialized }),

  clear: () =>
    set({
      user: null,
      accessToken: null,
      initialized: true,
    }),
}));
