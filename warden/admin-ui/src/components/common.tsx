/* common.tsx: small pieces every page uses (header, form dialog, select, loading / error / empty states). */
import { useState, type ReactNode } from "react";
import { toast } from "sonner";
import { AlertCircle, Loader2 } from "lucide-react";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle, DialogTrigger } from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";

export function PageHeader({ title, description, actions }: { title: string; description: string; actions?: ReactNode }) {
  return (
    <div className="flex flex-wrap items-end justify-between gap-4 pb-6">
      <div className="space-y-1">
        <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
        <p className="max-w-2xl text-sm text-muted-foreground">{description}</p>
      </div>
      {actions && <div className="flex flex-wrap gap-2">{actions}</div>}
    </div>
  );
}

// A button that opens a dialog with a form. `onSubmit` gets the form's fields; a refusal from Warden is
// shown inside the dialog (it stays open), success closes it with a toast.
export function FormDialog({ trigger, title, description, submit, success, onSubmit, children, variant, size }: {
  trigger: ReactNode; title: string; description?: string; submit: string; success: string;
  variant?: "default" | "outline" | "secondary" | "ghost"; size?: "default" | "sm" | "xs";
  onSubmit: (fields: Record<string, string>) => Promise<unknown>; children: ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  return (
    <Dialog open={open} onOpenChange={(o) => { setOpen(o); setError(null); }}>
      <DialogTrigger render={<Button variant={variant} size={size} />}>{trigger}</DialogTrigger>
      <DialogContent className="sm:max-w-lg">
        <form
          className="grid gap-5"
          onSubmit={async (ev) => {
            ev.preventDefault();
            const fields = Object.fromEntries(new FormData(ev.currentTarget)) as Record<string, string>;
            setBusy(true);
            setError(null);
            try {
              await onSubmit(fields);
              toast.success(success);
              setOpen(false);
            } catch (e) {
              setError((e as Error).message);
            } finally {
              setBusy(false);
            }
          }}
        >
          <DialogHeader>
            <DialogTitle>{title}</DialogTitle>
            {description && <DialogDescription>{description}</DialogDescription>}
          </DialogHeader>
          <div className="grid gap-4">{children}</div>
          {error && <Alert variant="destructive"><AlertCircle /><AlertTitle>Warden refused</AlertTitle><AlertDescription>{error}</AlertDescription></Alert>}
          <DialogFooter>
            <Button type="submit" disabled={busy}>{busy && <Loader2 className="animate-spin" />}{submit}</Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

export function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <div className="grid gap-2">
      <Label>{label}</Label>
      {children}
      {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
    </div>
  );
}

// A select that submits with its form under `name`.
export function SelectField({ name, items, placeholder, defaultValue, onChange, className }: {
  name?: string; items: { value: string; label: string }[]; placeholder?: string; defaultValue?: string;
  onChange?: (value: string) => void; className?: string;
}) {
  return (
    <Select name={name} items={items} defaultValue={defaultValue ?? null} onValueChange={(v) => onChange?.(v as string)}>
      <SelectTrigger className={className ?? "w-full"}><SelectValue placeholder={placeholder} /></SelectTrigger>
      <SelectContent>
        {items.map((i) => <SelectItem key={i.value} value={i.value}>{i.label}</SelectItem>)}
      </SelectContent>
    </Select>
  );
}

export function LoadError({ message }: { message: string }) {
  return <Alert variant="destructive"><AlertCircle /><AlertTitle>Couldn't load this</AlertTitle><AlertDescription>{message}</AlertDescription></Alert>;
}

export function Loading({ rows = 4 }: { rows?: number }) {
  return <div className="space-y-3">{Array.from({ length: rows }, (_, i) => <Skeleton key={i} className="h-10 w-full" />)}</div>;
}

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="rounded-lg border border-dashed p-10 text-center">
      <p className="font-medium">{title}</p>
      {children && <p className="mt-1 text-sm text-muted-foreground">{children}</p>}
    </div>
  );
}

// Run a change from a button; refusals become an error toast.
export async function act(work: () => Promise<unknown>, success: string, after: () => void) {
  try {
    await work();
    toast.success(success);
    after();
  } catch (e) {
    toast.error((e as Error).message);
  }
}

export const Mono = ({ children }: { children: ReactNode }) => <code className="font-mono text-xs text-muted-foreground">{children}</code>;
