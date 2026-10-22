/*
 * Member.cs — "this user works on this engagement, with this role".
 *
 * A join table between User and Engagement (one row per user per engagement).
 * No row = no access. The role decides what they may do there (step 7).
 *
 * C# notes:
 *   enum — a fixed set of named values (like Python's enum.Enum).
 *   Two types in one file is fine when they're this small and always used together.
 */
namespace Warden.Core.Domain;

public enum Role
{
    Staff,    // does the work: read documents, run vouching
    Manager,  // reviews the work
    Admin,    // manages the engagement (still blocked by ethical walls, step 3)
}

public class Member
{
    public Guid EngagementId { get; set; }
    public Guid UserId { get; set; }
    public Role Role { get; set; }
}
