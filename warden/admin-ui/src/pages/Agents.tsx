/* Agents: which AI agent apps the firm allows, and the most each may ever be given (its ceiling). */
import { useState } from "react";
import { Bot, Plus } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { act, Empty, Field, FormDialog, LoadError, Loading, Mono, PageHeader } from "@/components/common";
import { api, useData, type Agent, type Firm } from "@/lib/api";

function ScopeBoxes({ scopes, value, onChange, idPrefix }: { scopes: string[]; value: string[]; onChange: (v: string[]) => void; idPrefix: string }) {
  return (
    <div className="flex flex-wrap gap-x-5 gap-y-2">
      {scopes.map((s) => (
        <div key={s} className="flex items-center gap-2">
          <Checkbox id={`${idPrefix}-${s}`} checked={value.includes(s)}
            onCheckedChange={(on) => onChange(on ? [...value, s] : value.filter((x) => x !== s))} />
          <Label htmlFor={`${idPrefix}-${s}`} className="font-mono text-xs font-normal">{s}</Label>
        </div>
      ))}
    </div>
  );
}

function AgentRow({ agent, scopes, reload }: { agent: Agent; scopes: string[]; reload: () => void }) {
  const saved = agent.allowedScopes.split(" ").filter(Boolean);
  const [value, setValue] = useState(saved);
  const changed = [...value].sort().join(" ") !== [...saved].sort().join(" ");
  return (
    <TableRow>
      <TableCell>
        <div className="flex items-center gap-2 font-medium"><Bot className="size-4 text-muted-foreground" />{agent.name}</div>
        <Mono>{agent.entraClientId}</Mono>
      </TableCell>
      <TableCell>{saved.length ? <Badge variant="secondary" className="bg-emerald-500/10 text-emerald-700 dark:text-emerald-400">Active</Badge> : <Badge variant="outline">Off</Badge>}</TableCell>
      <TableCell><ScopeBoxes scopes={scopes} value={value} onChange={setValue} idPrefix={agent.id} /></TableCell>
      <TableCell className="text-right">
        <Button size="sm" variant={changed ? "default" : "outline"} disabled={!changed}
          onClick={() => act(() => api("PUT", `/admin/agents/${agent.entraClientId}`, { name: agent.name, allowedScopes: value.join(" ") }),
            value.length ? `${agent.name}'s ceiling saved` : `${agent.name} switched off`, reload)}>
          Save
        </Button>
      </TableCell>
    </TableRow>
  );
}

export default function Agents({ firm }: { firm: Firm }) {
  const agents = useData<Agent[]>("/admin/agents");
  const [newScopes, setNewScopes] = useState<string[]>([]);

  return (
    <>
      <PageHeader
        title="Agents"
        description="AI agent apps your firm allows, known by their Microsoft Entra app (client id). An agent gets at most its ceiling, and never more than the person it acts for. Untick everything to switch one off."
        actions={
          <FormDialog
            trigger={<><Plus />Allow agent</>}
            title="Allow an agent"
            description="Register the agent's app in Microsoft Entra first; its Application (client) ID goes here."
            submit="Allow agent" success="Agent allowed"
            onSubmit={async (f) => { await api("PUT", `/admin/agents/${f.clientId}`, { name: f.name, allowedScopes: newScopes.join(" ") }); setNewScopes([]); agents.reload(); }}
          >
            <Field label="Name"><Input name="name" required autoFocus placeholder="e.g. Claude" /></Field>
            <Field label="Client id"><Input name="clientId" required className="font-mono" placeholder="00000000-0000-0000-0000-000000000000" /></Field>
            <Field label="Ceiling"><ScopeBoxes scopes={firm.scopes} value={newScopes} onChange={setNewScopes} idPrefix="new" /></Field>
          </FormDialog>
        }
      />
      <Card>
        <CardContent>
          {agents.error ? <LoadError message={agents.error} /> : !agents.data ? <Loading /> : agents.data.length === 0 ? (
            <Empty title="No agents allowed yet">Until you allow one, every agent is refused as "unknown agent".</Empty>
          ) : (
            <Table>
              <TableHeader>
                <TableRow><TableHead>Agent</TableHead><TableHead>Status</TableHead><TableHead>Ceiling</TableHead><TableHead /></TableRow>
              </TableHeader>
              <TableBody>
                {agents.data.map((a) => <AgentRow key={`${a.id}-${a.allowedScopes}`} agent={a} scopes={firm.scopes} reload={agents.reload} />)}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
    </>
  );
}
