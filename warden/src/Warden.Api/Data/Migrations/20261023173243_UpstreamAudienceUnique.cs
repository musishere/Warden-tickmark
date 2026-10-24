using Microsoft.EntityFrameworkCore.Migrations;

#nullable disable

namespace Warden.Api.Data.Migrations
{
    /// <inheritdoc />
    public partial class UpstreamAudienceUnique : Migration
    {
        /// <inheritdoc />
        protected override void Up(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.CreateIndex(
                name: "ix_upstream_servers_audience",
                table: "upstream_servers",
                column: "audience",
                unique: true);
        }

        /// <inheritdoc />
        protected override void Down(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.DropIndex(
                name: "ix_upstream_servers_audience",
                table: "upstream_servers");
        }
    }
}
