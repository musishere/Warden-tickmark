/* Tool servers: the firm's MCP servers behind Warden, and which of their tools agents may use (and with what scope). */
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { EyeOff, Plug, Server as ServerIcon, Wrench } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Empty, Field, FormDialog, LoadError, Loading, Mono, PageHeader, SelectField } from "@/components/common";
import { api, useData, type Firm, type OfferedTool, type Server } from "@/lib/api";

const HIDDEN = "hidden";

function ToolsSheet({ server, scopes, onClose, onChanged }: { server: Server; scopes: string[]; onClose: () => void; onChanged: () => void }) {
  const [tools, setTools] = useState<OfferedTool[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = () => api<OfferedTool[]>("GET", `/admin/servers/${server.id}/tools`).then(setTools, (e: Error) => setError(e.message));
  useEffect(() => { load(); }, [server.id]);

  async function setScope(tool: string, scope: string) {
    const path = `/admin/servers/${server.id}/tools/${encodeURIComponent(tool)}`;
    try {
      if (scope === HIDDEN) await api("DELETE", path);
      else await api("PUT", path, { scope });
      toast.success(scope === HIDDEN ? `${tool} hidden from agents` : `${tool} usable with ${scope}`);
      load();
      onChanged();
    } catch (e) {
      toast.error((e as Error).message);
    }
  }

  const items = [{ value: HIDDEN, label: "Hidden" }, ...scopes.map((s) => ({ value: s, label: s }))];
  return (
    <Sheet open onOpenChange={(o) => !o && onClose()}>
      <SheetContent className="w-full overflow-y-auto data-[side=right]:sm:max-w-xl">
        <SheetHeader>
          <SheetTitle>What {server.name} offers</SheetTitle>
          <SheetDescription>
            Asked live from the server. A tool with no scope is hidden from agents and refused; tools the server adds later stay hidden until you assign them.
          </SheetDescription>
        </SheetHeader>
        <div className="px-4 pb-6">
          {error ? <LoadError message={error} /> : !tools ? <Loading rows={5} /> : tools.length === 0 ? <Empty title="The server offers no tools" /> : (
            <ul className="divide-y">
              {tools.map((t) => (
                <li key={t.name} className="flex items-center justify-between gap-4 py-3">
                  <div className="min-w-0">
                    <p className="flex items-center gap-2 font-mono text-sm">{t.scope ? <Wrench className="size-3.5" /> : <EyeOff className="size-3.5 text-muted-foreground" />}{t.name}</p>
                    {t.description && <p className="mt-0.5 text-xs text-muted-foreground">{t.description}</p>}
                  </div>
                  {t.takenBy ? <Badge variant="outline">Name used on {t.takenBy}</Badge> : (
                    <SelectField key={t.scope ?? HIDDEN} className="w-44" items={items} defaultValue={t.scope ?? HIDDEN} onChange={(v) => setScope(t.name, v)} />
                  )}
                </li>
              ))}
            </ul>
          )}
        </div>
      </SheetContent>
    </Sheet>
  );
}

export default function Servers({ firm }: { firm: Firm }) {
  const servers = useData<Server[]>("/admin/servers");
  const [open, setOpen] = useState<Server | null>(null);

  return (
    <>
      <PageHeader
        title="Tool servers"
        description="Your firm's MCP servers behind Warden. Agents never see their addresses or credentials: every call goes through Warden, which signs in to the server with a one-minute token for one tool."
        actions={
          <FormDialog
            trigger={<><Plug />Connect server</>}
            title="Connect an MCP server"
            description="The server must accept Warden's signed tokens (JWKS at /.well-known/jwks.json) for this audience, and nothing else. Its tools stay hidden until you assign them."
            submit="Connect" success="Server connected"
            onSubmit={async (f) => { await api("POST", "/admin/servers", f); servers.reload(); }}
          >
            <Field label="Name"><Input name="name" required autoFocus placeholder="e.g. documents" /></Field>
            <Field label="MCP address"><Input name="url" type="url" required className="font-mono" placeholder="https://…/mcp" /></Field>
            <Field label="Audience" hint="The token audience only this server accepts."><Input name="audience" required className="font-mono" placeholder="firm-a-documents" /></Field>
          </FormDialog>
        }
      />
      <Card>
        <CardContent>
          {servers.error ? <LoadError message={servers.error} /> : !servers.data ? <Loading /> : servers.data.length === 0 ? (
            <Empty title="No servers connected">Connect your firm's first MCP server.</Empty>
          ) : (
            <Table>
              <TableHeader>
                <TableRow><TableHead>Server</TableHead><TableHead>Tools agents may use</TableHead><TableHead /></TableRow>
              </TableHeader>
              <TableBody>
                {servers.data.map((s) => (
                  <TableRow key={s.id}>
                    <TableCell>
                      <div className="flex items-center gap-2 font-medium"><ServerIcon className="size-4 text-muted-foreground" />{s.name}</div>
                      <Mono>{s.url}</Mono>
                      <div className="text-xs text-muted-foreground">audience <span className="font-mono">{s.audience}</span></div>
                    </TableCell>
                    <TableCell>
                      <div className="flex flex-wrap gap-1.5">
                        {s.tools.length === 0 && <span className="text-sm text-muted-foreground">None yet: all hidden</span>}
                        {s.tools.map((t) => <Badge key={t.name} variant="secondary" className="font-mono">{t.name} · {t.scope}</Badge>)}
                      </div>
                    </TableCell>
                    <TableCell className="text-right"><Button variant="outline" size="sm" onClick={() => setOpen(s)}><Wrench />Tools</Button></TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
      {open && <ToolsSheet server={open} scopes={firm.scopes} onClose={() => setOpen(null)} onChanged={servers.reload} />}
    </>
  );
}
