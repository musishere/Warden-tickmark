/* Engagements & walls: each engagement's team, and who is walled off it (beats every role, even Admin). */
import { BrickWall, FolderPlus, ShieldBan, Unlock } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { act, Empty, Field, FormDialog, LoadError, Loading, PageHeader, SelectField } from "@/components/common";
import { api, useData, type Engagement, type Person } from "@/lib/api";

export default function Engagements() {
  const engagements = useData<Engagement[]>("/admin/engagements");
  const people = useData<Person[]>("/admin/people");

  return (
    <>
      <PageHeader
        title="Engagements & walls"
        description="An ethical wall keeps a person, and every agent acting for them, out of an engagement whatever their role. Refusals show the reason you give here."
        actions={engagements.data && people.data && (
          <>
            <FormDialog
              variant="outline"
              trigger={<><ShieldBan />Add wall</>}
              title="Wall someone off an engagement"
              description="Takes effect at their agents' next call. The reason appears in every refusal and in the audit log."
              submit="Set wall" success="Wall set"
              onSubmit={async (f) => { await api("PUT", `/admin/engagements/${f.engagement}/walls/${f.person}`, { reason: f.reason }); engagements.reload(); }}
            >
              <Field label="Person"><SelectField name="person" placeholder="Choose a person" items={people.data.map((p) => ({ value: p.id, label: p.name }))} /></Field>
              <Field label="Engagement"><SelectField name="engagement" placeholder="Choose an engagement" items={engagements.data.map((e) => ({ value: e.id, label: e.name }))} /></Field>
              <Field label="Reason" hint='e.g. "Works on Coke 2026"'><Input name="reason" required /></Field>
            </FormDialog>
            <FormDialog
              trigger={<><FolderPlus />New engagement</>}
              title="Open an engagement"
              submit="Open engagement" success="Engagement opened"
              onSubmit={async (f) => { await api("POST", "/admin/engagements", { name: f.name }); engagements.reload(); }}
            >
              <Field label="Name" hint='e.g. "Coke 2026"'><Input name="name" required autoFocus /></Field>
            </FormDialog>
          </>
        )}
      />
      {engagements.error ? <LoadError message={engagements.error} /> : !engagements.data ? <Loading rows={3} /> : engagements.data.length === 0 ? (
        <Empty title="No engagements yet">Open one, then put people on it from the People page.</Empty>
      ) : (
        <div className="grid gap-4 lg:grid-cols-2">
          {engagements.data.map((e) => (
            <Card key={e.id}>
              <CardHeader>
                <CardTitle>{e.name}</CardTitle>
                <CardDescription className="font-mono text-xs">{e.id}</CardDescription>
              </CardHeader>
              <CardContent className="space-y-5">
                <div className="space-y-2">
                  <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Team</p>
                  <div className="flex flex-wrap gap-1.5">
                    {e.members.length === 0 && <span className="text-sm text-muted-foreground">Nobody yet</span>}
                    {e.members.map((m) => <Badge key={m.userId} variant="secondary">{m.name} · {m.role}</Badge>)}
                  </div>
                </div>
                <div className="space-y-2">
                  <p className="flex items-center gap-1.5 text-xs font-medium uppercase tracking-wide text-muted-foreground"><BrickWall className="size-3.5" />Ethical walls</p>
                  {e.walls.length === 0 && <p className="text-sm text-muted-foreground">No walls</p>}
                  <ul className="space-y-2">
                    {e.walls.map((w) => (
                      <li key={w.userId} className="flex items-center justify-between gap-3 rounded-lg border border-destructive/30 bg-destructive/5 px-3 py-2">
                        <div className="min-w-0">
                          <p className="text-sm font-medium">{w.name}</p>
                          <p className="truncate text-xs text-muted-foreground">{w.reason}</p>
                        </div>
                        <Button variant="ghost" size="sm"
                          onClick={() => act(() => api("DELETE", `/admin/engagements/${e.id}/walls/${w.userId}`), `Wall lifted for ${w.name}`, engagements.reload)}>
                          <Unlock />Lift
                        </Button>
                      </li>
                    ))}
                  </ul>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}
    </>
  );
}
