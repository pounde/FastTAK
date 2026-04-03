# Group Management UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add group CRUD and user-group assignment UI to the existing Users dashboard page.

**Architecture:** All changes are in `users.html` — no new files, routes, or API endpoints. A groups card handles create/delete, and a checkbox dropdown in the user detail panel handles assignment. Patterns are copied from `service_accounts.html`.

**Tech Stack:** Alpine.js, HTMX, Jinja2 templates, existing `/api/groups` and `/api/users/{id}/groups` endpoints.

---

## File Structure

- Modify: `monitor/app/dashboard/templates/users.html` — all UI changes (Alpine.js state, groups card, detail panel groups section)

No new files. No API changes. No test files (this is template-only UI work tested manually).

---

### Task 1: Add Alpine.js state and methods for group management

**Files:**
- Modify: `monitor/app/dashboard/templates/users.html:6-213` (the `x-data` block)

- [ ] **Step 1: Add group state variables to `x-data`**

Add these variables after the existing `passwordValue: ''` declaration (line 21):

```javascript
groups: [],
showGroups: false,
newGroupName: '',
groupLoading: false,
editGroups: [],
editGroupDropdownOpen: false,
```

- [ ] **Step 2: Add `init()` method**

Add this method immediately before the existing `refreshList()` method:

```javascript
async init() {
    await this.fetchGroups();
},
```

Alpine.js auto-calls `init()` when it exists in `x-data` — no `x-init` directive needed.

- [ ] **Step 3: Add `fetchGroups()` method**

Add after `init()`:

```javascript
async fetchGroups() {
    try {
        const r = await fetch('/api/groups');
        if (r.ok) this.groups = await r.json();
    } catch(e) { /* groups will be empty */ }
},
```

- [ ] **Step 4: Add `createGroup()` method**

Add after `fetchGroups()`:

```javascript
async createGroup() {
    const name = this.newGroupName.trim();
    if (!name) return;
    this.groupLoading = true;
    try {
        const r = await fetch('/api/groups', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({name})
        });
        if (!r.ok) {
            const d = await r.json();
            alert(d.detail || 'Failed to create group');
            return;
        }
        this.newGroupName = '';
        await this.fetchGroups();
        this.refreshList();
    } catch(e) { alert(e.message); }
    finally { this.groupLoading = false; }
},
```

- [ ] **Step 5: Add `deleteGroup(group)` method**

Add after `createGroup()`:

```javascript
async deleteGroup(group) {
    if (!confirm('Delete group "' + group.name + '"? Users in this group will lose membership.')) return;
    this.groupLoading = true;
    try {
        const r = await fetch('/api/groups/' + group.id, { method: 'DELETE' });
        if (!r.ok) {
            const d = await r.json();
            alert(d.detail || 'Failed to delete group');
            return;
        }
        await this.fetchGroups();
        this.refreshList();
    } catch(e) { alert(e.message); }
    finally { this.groupLoading = false; }
},
```

- [ ] **Step 6: Add `toggleEditGroup(name)` method**

Add after `deleteGroup()`:

```javascript
toggleEditGroup(name) {
    const idx = this.editGroups.indexOf(name);
    if (idx === -1) this.editGroups.push(name);
    else this.editGroups.splice(idx, 1);
},
```

- [ ] **Step 7: Update `selectUser()` to initialize `editGroups`**

In the existing `selectUser()` method (currently around line 196), add two lines inside the `else` block, after `this.passwordValue = '';` and before `this.certName = '';`:

```javascript
this.editGroups = [...(user.groups || [])];
this.editGroupDropdownOpen = false;
```

- [ ] **Step 8: Replace `saveEdit()` with version that includes group changes**

Replace the entire existing `saveEdit()` method with this complete version. It adds a `groupsChanged` check and a `PUT /api/users/{id}/groups` call after the PATCH succeeds:

```javascript
async saveEdit() {
    if (!this.selectedUser) return;
    this.actionLoading = true; this.actionResult = ''; this.actionError = false;
    try {
        const body = {};
        if (this.editName && this.editName !== this.selectedUser.name) body.name = this.editName;
        if (this.editTtl !== '') body.ttl_hours = this.editTtl === 'clear' ? null : parseInt(this.editTtl);
        const groupsChanged = JSON.stringify([...this.editGroups].sort()) !== JSON.stringify([...(this.selectedUser.groups || [])].sort());

        if (Object.keys(body).length === 0 && !groupsChanged) {
            this.actionResult = 'No changes';
            return;
        }

        // PATCH name/TTL if changed
        if (Object.keys(body).length > 0) {
            const r = await fetch(`/api/users/${this.selectedUser.id}`, {
                method: 'PATCH',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(body)
            });
            if (!r.ok) {
                const d = await r.json();
                this.actionResult = d.detail || 'Update failed';
                this.actionError = true;
                return;
            }
        }

        // PUT groups if changed
        if (groupsChanged) {
            const gr = await fetch(`/api/users/${this.selectedUser.id}/groups`, {
                method: 'PUT',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({groups: [...this.editGroups]})
            });
            if (!gr.ok) {
                const d = await gr.json();
                this.actionResult = d.detail || 'Group update failed';
                this.actionError = true;
                return;
            }
        }

        this.actionResult = 'Updated';
        this.refreshList();
    } catch(e) { this.actionResult = e.message; this.actionError = true; }
    finally { this.actionLoading = false; }
},
```

- [ ] **Step 9: Verify page loads without errors**

Run: `just dev-up` (or reload if already running), navigate to the Users page, open browser console.
Expected: No JavaScript errors. Page loads normally. Groups fetched (check Network tab for `GET /api/groups`).

- [ ] **Step 10: Commit**

```bash
git add monitor/app/dashboard/templates/users.html
git commit -m "feat: add Alpine.js state and methods for group management"
```

---

### Task 2: Add groups card UI to the Users page

**Files:**
- Modify: `monitor/app/dashboard/templates/users.html` (HTML section, after the search bar)

- [ ] **Step 1: Add "Groups" toggle button to the top bar**

Replace the entire top flex row (the `<div class="flex mb-16" ...>` through its closing `</div>`) with this complete version. The Groups button goes on the right side next to New User:

```html
<!-- Search + Groups + Create toggle -->
<div class="flex mb-16" style="justify-content: space-between;">
    <div class="flex">
        <input type="text" id="user-search" placeholder="Search users..."
               @keyup.debounce.300ms="refreshList()"
               style="width: 280px;">
        <button @click="refreshList()">Search</button>
    </div>
    <div class="flex" style="gap: 8px;">
        <button @click="showGroups = !showGroups">
            <span x-text="showGroups ? 'Hide Groups' : 'Groups'"></span>
        </button>
        <button @click="showCreate = !showCreate; createError = ''">
            <span x-text="showCreate ? 'Cancel' : 'New User'"></span>
        </button>
    </div>
</div>
```

- [ ] **Step 2: Add the groups card**

Add this card between the "Create User" card (around line 251) and the "User list" card. Place it right before `<!-- User list (HTMX partial) -->`:

```html
<!-- Groups card (collapsible) -->
<div class="card" x-show="showGroups" x-cloak style="margin-bottom: 16px;">
    <div class="card-title">Groups</div>

    <!-- Create group -->
    <div class="flex mb-8" style="gap: 8px;">
        <input type="text" x-model="newGroupName" placeholder="New group name"
               @keyup.enter="createGroup()" style="flex: 1; max-width: 280px;">
        <button :disabled="!newGroupName.trim() || groupLoading" @click="createGroup()">
            <span x-text="groupLoading ? '...' : 'Create'"></span>
        </button>
    </div>

    <!-- Group list -->
    <template x-if="groups.length > 0">
        <div>
            <template x-for="g in groups" :key="g.id">
                <div class="flex" style="margin-bottom: 6px; gap: 12px; align-items: center;">
                    <span style="font-size: 13px;" x-text="g.name"></span>
                    <a href="#" style="color: #ef5350; font-size: 11px;"
                       @click.prevent="deleteGroup(g)">delete</a>
                </div>
            </template>
        </div>
    </template>
    <p x-show="groups.length === 0" class="text-muted" style="font-size: 12px;">
        No groups. Create one to organize users into TAK channels.
    </p>
</div>
```

- [ ] **Step 3: Verify groups card works**

Run: Reload the Users page, click "Groups" button.
Expected: Groups card appears. If groups exist, they're listed with delete links. Create form accepts input. Creating a group adds it to the list. Deleting a group removes it.

- [ ] **Step 4: Commit**

```bash
git add monitor/app/dashboard/templates/users.html
git commit -m "feat: add groups card with create/delete on users page"
```

---

### Task 3: Add group assignment dropdown inside Edit section

**Files:**
- Modify: `monitor/app/dashboard/templates/users.html` (Edit section of the detail panel)

- [ ] **Step 1: Add groups checkbox dropdown inside the Edit section**

In the detail panel's Edit section, add the groups dropdown between the TTL input (`<div class="flex mb-8">` with `editTtl`) and the Save button. This matches the service_accounts.html pattern (lines 275-300) where groups live inside Edit, right above Save.

```html
<!-- Group editor (checkbox dropdown) -->
<div class="mb-8" style="position: relative;">
    <label style="color: #888; font-size: 12px; display: block; margin-bottom: 4px;">Groups</label>
    <button type="button" @click="editGroupDropdownOpen = !editGroupDropdownOpen"
            style="min-width: 180px; text-align: left; position: relative; padding-right: 24px; width: 100%;">
        <span x-text="editGroups.length ? editGroups.join(', ') : 'No groups'"
              :style="editGroups.length ? '' : 'color: #555'"></span>
        <span style="position: absolute; right: 8px; top: 50%; transform: translateY(-50%); color: #555;">&#9662;</span>
    </button>
    <div x-show="editGroupDropdownOpen" x-cloak @click.outside="editGroupDropdownOpen = false"
         style="position: absolute; top: 100%; left: 0; z-index: 20; background: #1a1a1a; border: 1px solid #3a3a3a; border-radius: 4px; max-height: 200px; overflow-y: auto; min-width: 180px; margin-top: 4px; width: 100%;">
        <template x-if="groups.length === 0">
            <p style="padding: 8px 12px; color: #555; font-size: 12px; margin: 0;">No groups available</p>
        </template>
        <template x-for="g in groups" :key="g.name">
            <label style="display: flex; align-items: center; gap: 8px; padding: 6px 12px; cursor: pointer; font-size: 13px;"
                   @click.stop>
                <input type="checkbox"
                       :checked="editGroups.includes(g.name)"
                       @change="toggleEditGroup(g.name)"
                       style="accent-color: #4fc3f7;">
                <span x-text="g.name"></span>
            </label>
        </template>
    </div>
</div>
```

The resulting Edit section order should be: Name input, TTL input, Groups dropdown, Save button.

- [ ] **Step 2: Verify group assignment works end-to-end**

Run: Reload Users page. Click a user to open detail panel.
Expected:
1. Groups dropdown appears in the Edit section, above the Save button
2. Current groups are pre-checked
3. Toggling groups and clicking Save persists changes
4. Refreshing page and re-selecting user shows updated groups
5. Creating a new group in the groups card makes it appear in the dropdown immediately

- [ ] **Step 3: Commit**

```bash
git add monitor/app/dashboard/templates/users.html
git commit -m "feat: add group assignment dropdown to user detail panel"
```
