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
