import { create } from 'zustand';
import {
  fetchAuthConfig,
  fetchMe,
  logout as apiLogout,
  type AuthUser,
} from '@/api/auth';

interface AuthState {
  user: AuthUser | null;
  phoneAuthEnabled: boolean;
  loading: boolean;
  initialized: boolean;
  banned: boolean;
  banMessage: string | null;
  init: () => Promise<void>;
  refreshUser: () => Promise<AuthUser | null>;
  setUser: (user: AuthUser | null) => void;
  logout: () => Promise<void>;
}

export const useAuthStore = create<AuthState>((set, get) => ({
  user: null,
  phoneAuthEnabled: false,
  loading: true,
  initialized: false,
  banned: false,
  banMessage: null,

  init: async () => {
    if (get().initialized) return;
    set({ loading: true });
    try {
      const config = await fetchAuthConfig();
      if (!config.phone_auth_enabled) {
        set({
          phoneAuthEnabled: false,
          user: null,
          banned: false,
          banMessage: null,
          loading: false,
          initialized: true,
        });
        return;
      }
      const me = await fetchMe();
      set({
        phoneAuthEnabled: true,
        user: me.user,
        banned: me.banned,
        banMessage: me.banMessage,
        loading: false,
        initialized: true,
      });
    } catch {
      set({ loading: false, initialized: true });
    }
  },

  refreshUser: async () => {
    const me = await fetchMe();
    set({
      user: me.user,
      banned: me.banned,
      banMessage: me.banMessage,
    });
    return me.user;
  },

  setUser: (user) => set({ user }),

  logout: async () => {
    await apiLogout();
    set({ user: null, banned: false, banMessage: null });
  },
}));
