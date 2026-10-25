/* People: the firm's people, their Microsoft accounts, and which engagements they're on in which role. */
import { Link2, UserPlus, Users, X } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { act, Empty, Field, FormDialog, LoadError, Loading, Mono, PageHeader, SelectField } from "@/components/common";
import { api, useData, type Engagement, type Firm, type Person } from "@/lib/api";

const OBJECT_ID_HINT = "Microsoft Entra admin center → Users → the person → Object ID.";

export default function People({ firm }: { firm: Firm }) {
  const people = useData<Person[]>("/admin/people");
  const engagements = useData<Engagement[]>("/admin/engagements");

  return (
    <>
      <PageHeader
        title="People"
        description="Who works at the firm, the Microsoft account each signs in with, and their role on each engagement. A person's agents can never do more than they can."
        actions={people.data && engagements.data && (
          <>
            <FormDialog
              variant="outline"
              trigger={<><Users />Assign to engagement</>}
              title="Assign to an engagement"
              description="Puts the person on the engagement with this role, or changes their role. Takes effect at their agents' next call."
              submit="Save" success="Membership saved"
              onSubmit={async (f) => { await api("PUT", `/admin/engagements/${f.engagement}/members/${f.person}`, { role: f.role }); people.reload(); }}
            >
              <Field label="Person"><SelectField name="person" placeholder="Choose a person" items={people.data.map((p) => ({ value: p.id, label: p.name }))} /></Field>
              <Field label="Engagement"><SelectField name="engagement" placeholder="Choose an engagement" items={engagements.data.map((e) => ({ value: e.id, label: e.name }))} /></Field>
              <Field label="Role"><SelectField name="role" defaultValue="Staff" items={firm.roles.map((r) => ({ value: r, label: r }))} /></Field>
            </FormDialog>
            <FormDialog
              trigger={<><UserPlus />Add person</>}
              title="Add a person"
              description="They can sign in once their Microsoft account is linked."
              submit="Add person" success="Person added"
              onSubmit={async (f) => {
                await api("POST", "/admin/people", { name: f.name, email: f.email, entraObjectId: f.entraObjectId || null });
                people.reload();
              }}
            >
              <Field label="Name"><Input name="name" required autoFocus /></Field>
              <Field label="Email"><Input name="email" type="email" required /></Field>
              <Field label="Microsoft account (Object ID)" hint={`Optional. ${OBJECT_ID_HINT}`}><Input name="entraObjectId" className="font-mono" placeholder="00000000-0000-0000-0000-000000000000" /></Field>
            </FormDialog>
          </>
        )}
      />
      <Card>
        <CardContent>
          {people.error ? <LoadError message={people.error} /> : !people.data ? <Loading /> : people.data.length === 0 ? (
            <Empty title="No people yet">Add the first person to get started.</Empty>
          ) : (
            <Table>
              <TableHeader>
                <TableRow><TableHead>Person</TableHead><TableHead>Microsoft account</TableHead><TableHead>Engagements</TableHead></TableRow>
              </TableHeader>
              <TableBody>
                {people.data.map((p) => (
                  <TableRow key={p.id}>
                    <TableCell>
                      <div className="flex items-center gap-2 font-medium">{p.name}{!p.active && <Badge variant="destructive">Deactivated</Badge>}</div>
                      <div className="text-xs text-muted-foreground">{p.email}</div>
                    </TableCell>
                    <TableCell>
                      {p.entraObjectId ? <Mono>{p.entraObjectId}</Mono> : (
                        <FormDialog
                          variant="outline" size="sm"
                          trigger={<><Link2 />Link account</>}
                          title={`Link ${p.name}'s Microsoft account`}
                          description={OBJECT_ID_HINT}
                          submit="Link" success="Account linked"
                          onSubmit={async (f) => { await api("PUT", `/admin/people/${p.id}/entra`, { objectId: f.objectId }); people.reload(); }}
                        >
                          <Field label="Object ID"><Input name="objectId" required autoFocus className="font-mono" placeholder="00000000-0000-0000-0000-000000000000" /></Field>
                        </FormDialog>
                      )}
                    </TableCell>
                    <TableCell>
                      <div className="flex flex-wrap gap-1.5">
                        {p.engagements.length === 0 && <span className="text-sm text-muted-foreground">None</span>}
                        {p.engagements.map((e) => (
                          <Badge key={e.id} variant="secondary" className="gap-1 pr-0.5">
                            {e.name} · {e.role}
                            <Tooltip>
                              <TooltipTrigger render={
                                <Button variant="ghost" size="icon-xs" aria-label={`Take ${p.name} off ${e.name}`}
                                  onClick={() => act(() => api("DELETE", `/admin/engagements/${e.id}/members/${p.id}`), `${p.name} taken off ${e.name}`, people.reload)} />
                              }><X /></TooltipTrigger>
                              <TooltipContent>Take off {e.name}</TooltipContent>
                            </Tooltip>
                          </Badge>
                        ))}
                      </div>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
    </>
  );
}
