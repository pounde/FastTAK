# Group Management UI — Design Spec

## Goal

Add group CRUD and user-group assignment to the Users dashboard page. The API already supports all operations (`/api/groups` CRUD, `PUT /api/users/{id}/groups`). This is a dashboard-only feature.

## Architecture

All group management lives on the existing Users page (`users.html`). No new pages, routes, or API endpoints. Two additions:

1. **Groups card** — a collapsible card on the Users page for creating and deleting groups
2. **Groups section in user detail panel** — checkbox dropdown for assigning groups to a user, merged into the existing save flow

## Components

### Groups Card

The "Groups" toggle button goes in the existing top flex row alongside the search bar and "New User" button. The collapsible card appears below that row (same position as the "Create User" card).

- Add an `async init()` method to the page's `x-data` that calls `fetchGroups()` on load
- Displays a flat list: group name + delete button per row
- Empty state: "No groups. Create one to organize users into TAK channels."
- Create form: text input + "Create" button, calls `POST /api/groups`
  - Validate: non-empty after trimming whitespace. API handles duplicate errors.
- Delete: confirmation prompt, calls `DELETE /api/groups/{id}`
- After create/delete: re-fetch the shared `groups` array (used by both the card and the user detail dropdown) AND refresh the user list (deleting a group silently removes members)

### User Detail Panel — Groups Section

Added to the detail panel's grid (alongside Edit, Password, Enrollment, Certificates, Actions).

- Checkbox dropdown listing all available groups (reuse pattern from `service_accounts.html` lines 275-294)
- `selectUser()` must initialize `editGroups = [...(user.groups || [])]` and reset `editGroupDropdownOpen = false`
- Group save is **merged into the existing `saveEdit()`** — if groups changed, call `PUT /api/users/{id}/groups` alongside the `PATCH`. This matches the service accounts pattern.
- Errors from the groups PUT use the existing `actionResult`/`actionError` feedback

## Data Flow

- `GET /api/groups` returns `[{id, name}, ...]` — group names stripped of `tak_` prefix by the API
- `POST /api/groups` with `{name}` — API adds `tak_` prefix
- `DELETE /api/groups/{id}` — by Authentik group UUID
- `PUT /api/users/{id}/groups` with `{groups: ["name1", "name2"]}` — full replacement
- User objects already include `groups: [...]` array from the list endpoint

## UI State

New Alpine.js state variables on the Users page `x-data`:

```
groups: [],                  // all available groups (shared by card + dropdown)
showGroups: false,           // toggle groups card visibility
newGroupName: '',            // create group input
groupLoading: false,         // loading state for group operations

// User detail additions
editGroups: [],              // selected groups for current user
editGroupDropdownOpen: false // dropdown state
```

New methods:

```
async fetchGroups()          // GET /api/groups, populates groups[]
async createGroup()          // POST /api/groups, then fetchGroups() + refreshList()
async deleteGroup(id)        // DELETE /api/groups/{id}, then fetchGroups() + refreshList()
toggleEditGroup(name)        // toggle group in editGroups[] (checkbox handler)
```

`saveEdit()` updated to also call `PUT /api/users/{id}/groups` when `editGroups` differs from `selectedUser.groups`.

## Testing

The group API endpoints are already tested. This feature needs:

- Manual verification that group CRUD works from the dashboard
- Manual verification that user-group assignment persists correctly
- No new API tests required (API is unchanged)

## Scope Exclusions

- No group membership count or member list (keep it simple)
- No drag-and-drop or bulk assignment
- No group rename (delete + recreate is fine for now)
