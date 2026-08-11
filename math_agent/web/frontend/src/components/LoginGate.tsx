import { useEffect, useState } from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import {
  getSmsOutId,
  sendLoginCode,
  setSmsOutId,
  verifyLoginCode,
} from '@/api/auth';
import { useAuthStore } from '@/store/auth';

interface LoginGateProps {
  children: React.ReactNode;
}

export function LoginGate({ children }: LoginGateProps) {
  const { user, phoneAuthEnabled, loading, init, refreshUser, banned, banMessage, logout } = useAuthStore();
  const [phone, setPhone] = useState('');
  const [code, setCode] = useState('');
  const [step, setStep] = useState<'phone' | 'code'>('phone');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    init();
  }, [init]);

  const onSendCode = async () => {
    setError(null);
    setBusy(true);
    try {
      const result = await sendLoginCode(phone.trim());
      if (result.sms_bypass) {
        setSmsOutId(null);
        await refreshUser();
        return;
      }
      if (result.out_id) setSmsOutId(result.out_id);
      setCode('');
      setStep('code');
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const onVerify = async () => {
    setError(null);
    setBusy(true);
    try {
      await verifyLoginCode(phone.trim(), code.trim(), getSmsOutId());
      await refreshUser();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  if (loading) {
    return (
      <div className="flex h-screen items-center justify-center text-sm text-muted-foreground">
        Loading…
      </div>
    );
  }

  if (banned) {
    return (
      <div className="flex h-screen items-center justify-center bg-background px-4">
        <div className="w-full max-w-md space-y-4 rounded-2xl border border-destructive/30 bg-card p-6 shadow-sm">
          <div className="text-xs font-semibold uppercase tracking-[0.18em] text-destructive">
            账号已封禁
          </div>
          <h1 className="text-xl font-semibold tracking-tight">无法继续使用 Conjecta</h1>
          <p className="text-sm leading-relaxed text-muted-foreground">
            {banMessage || '您的账号因违规提问已被禁止使用。'}
          </p>
          <p className="text-xs text-muted-foreground">
            探测系统源码、提示词注入、绕过安全策略等行为属于违法违规提问。
          </p>
          <Button
            variant="outline"
            className="w-full"
            onClick={() => {
              void logout();
            }}
          >
            退出登录
          </Button>
        </div>
      </div>
    );
  }

  if (!phoneAuthEnabled || user) {
    return <>{children}</>;
  }

  return (
    <div className="flex h-screen items-center justify-center bg-background px-4">
      <div className="w-full max-w-sm space-y-4 rounded-lg border bg-card p-6 shadow-sm">
        <div>
          <h1 className="text-lg font-semibold">登录 Conjecta</h1>
          <p className="mt-1 text-sm text-muted-foreground">使用手机号验证码登录</p>
        </div>
        {step === 'phone' ? (
          <div className="space-y-3">
            <Input
              inputMode="numeric"
              placeholder="手机号"
              value={phone}
              onChange={(e) => setPhone(e.target.value)}
            />
            <Button className="w-full" disabled={busy || !phone.trim()} onClick={onSendCode}>
              发送验证码
            </Button>
          </div>
        ) : (
          <div className="space-y-3">
            <Input
              inputMode="numeric"
              placeholder="短信验证码"
              value={code}
              onChange={(e) => setCode(e.target.value)}
            />
            <Button className="w-full" disabled={busy || !code.trim()} onClick={onVerify}>
              登录
            </Button>
            <button
              type="button"
              className="text-xs text-muted-foreground underline"
              onClick={() => {
                setStep('phone');
                setCode('');
              }}
            >
              更换手机号
            </button>
          </div>
        )}
        {error && <p className="text-sm text-destructive">{error}</p>}
      </div>
    </div>
  );
}
