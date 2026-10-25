/*
 * HealthTests.cs — smoke test: the app starts and GET /health returns 200.
 *
 * xUnit basics (like pytest):
 *   [Fact]  — marks a test method (like def test_...).
 *   Assert.* — like assert in pytest.
 * WebApplicationFactory<Program> starts the whole API in memory, no real port,
 * and CreateClient() gives an HttpClient pointed at it (like FastAPI's TestClient).
 * IClassFixture shares one running app across all tests in this class.
 */
using Microsoft.AspNetCore.Mvc.Testing;
using Xunit;

namespace Warden.Tests;

public class HealthTests(WebApplicationFactory<Program> factory) : IClassFixture<WebApplicationFactory<Program>>
{
    [Fact]
    public async Task health_returns_ok()
    {
        var res = await factory.CreateClient().GetAsync("/health");
        Assert.True(res.IsSuccessStatusCode);
    }
}
