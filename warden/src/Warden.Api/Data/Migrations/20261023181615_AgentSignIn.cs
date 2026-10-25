using System;
using Microsoft.EntityFrameworkCore.Migrations;

#nullable disable

namespace Warden.Api.Data.Migrations
{
    /// <inheritdoc />
    public partial class AgentSignIn : Migration
    {
        /// <inheritdoc />
        protected override void Up(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.AddColumn<string>(
                name: "sign_in_id",
                table: "delegated_tokens",
                type: "text",
                nullable: true);

            migrationBuilder.AddColumn<Guid>(
                name: "entra_client_id",
                table: "agents",
                type: "uuid",
                nullable: true);

            migrationBuilder.CreateIndex(
                name: "ix_delegated_tokens_sign_in_id",
                table: "delegated_tokens",
                column: "sign_in_id");

            migrationBuilder.CreateIndex(
                name: "ix_agents_entra_client_id",
                table: "agents",
                column: "entra_client_id",
                unique: true);

            // Hand-added: a database seeded before this step gets Tickmark's demo client id
            // (DemoSeed.TickmarkClientId), so its sign-ins still find it.
            migrationBuilder.Sql("UPDATE agents SET entra_client_id = 'c1000000-0000-0000-0000-000000000001' WHERE id = 'a9e00000-0000-0000-0000-000000000001';");
        }

        /// <inheritdoc />
        protected override void Down(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.DropIndex(
                name: "ix_delegated_tokens_sign_in_id",
                table: "delegated_tokens");

            migrationBuilder.DropIndex(
                name: "ix_agents_entra_client_id",
                table: "agents");

            migrationBuilder.DropColumn(
                name: "sign_in_id",
                table: "delegated_tokens");

            migrationBuilder.DropColumn(
                name: "entra_client_id",
                table: "agents");
        }
    }
}
