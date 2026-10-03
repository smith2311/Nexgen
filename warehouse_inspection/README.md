# Warehouse Inspection Module

## Purpose

The Warehouse Inspection module is a mobile-first inspection workflow for warehouse teams. It helps users inspect a selected building, condition category, and floor, answer each checklist point, attach live proof photos, and submit a traceable inspection record.

The module is designed for operational accuracy, fast field entry, and strong audit evidence.

## Platform Theme

The module should follow the NexGen / Meril operational platform theme:

- Clean, professional, warehouse-focused interface.
- Mobile-first layouts with large touch targets and readable checklist rows.
- Primary colors: blue / indigo for navigation and main actions.
- Supporting colors: green for success, red for failed or risk states, amber for warnings, and neutral gray / white for surfaces.
- Visual style should feel reliable, compliance-oriented, and easy to use on the warehouse floor.
- Avoid decorative screens; the first screen should quickly move the user into inspection work.

## User Roles

### Inspection User

The regular warehouse user can:

- Log in with authorized Warehouse Inspection access.
- Open the inspection form directly.
- Select building, condition category, and floor.
- Complete all required checklist points.
- Capture live photos as proof.
- Submit inspection responses.
- View submission success or error status.

### Warehouse Inspection Admin

The admin user can:

- Manage buildings / warehouses.
- Manage floors.
- Manage condition categories.
- Manage checklist checkpoints.
- Configure which checkpoints apply to which floors.
- Configure responsible email mappings for floor/checkpoint follow-up.
- Import checklist master data from Excel.
- Manage Warehouse Inspection user access.
- View dashboard/admin tools.

## Main User Flow

1. User logs in to the mobile app.
2. User selects a warehouse/building.
3. User selects an inspection condition/category.
4. User selects the floor to inspect.
5. App loads only the checklist points applicable to that floor.
6. User answers each checkpoint with `Yes` or `No`.
7. User optionally adds comments/remarks.
8. User captures live proof photos where required or useful.
9. App validates that all required checklist items are completed.
10. User submits the inspection.
11. System stores the submission with user, timestamp, floor, condition, responses, comments, and photo references.
12. System sends configured inspection email notifications and summary reports.

## Module Functionalities

### Authentication and Access

- Role-based access for Warehouse Inspection users and admins.
- Access is intended for authorized company users.
- Admin and user permissions are separate.
- Regular users should see only the inspection workflow.
- Admin users should see setup and management tools.

### Building / Warehouse Selection

- Users select the active building or warehouse before starting.
- Inactive buildings should not appear for regular inspection users.
- Each building has its own floors, condition categories, and checklist configuration.

### Floor Selection

- Floors are configured per building.
- Only active floors are available for inspection.
- Checklist items are filtered by selected floor.

### Condition Categories

- Condition categories group the inspection checklist.
- Examples may include housekeeping, safety, storage condition, equipment condition, documentation, or other warehouse-specific checks.
- Categories are maintained by admins.

### Checkpoints / Checklist Items

- Each condition category contains checkpoint questions.
- Each checkpoint can be active or inactive.
- Checkpoints can be mapped to specific floors.
- Each checkpoint supports a `Yes` / `No` inspection response.
- Comments can be captured against checklist responses.

### Live Photo Evidence

Live proof photo capture is a core rule of this mobile app.

- The app must allow photo capture only from the device camera in real time.
- Users must not be allowed to upload from gallery/photos.
- Users must not be allowed to paste or attach images from clipboard.
- Users must not be allowed to import image files from device storage.
- The UI should expose a camera action only, such as `Capture Photo`.
- Any captured proof photo should be linked to its checkpoint before final submission.
- The backend photo size expectation is 400 KB to 5 MB, so the mobile app should compress or guide retake within that range.

### Draft and Progress Handling

- The app should preserve current checklist progress while the user moves between floors or network requests.
- The submit action should stay disabled until all required checklist points are answered.
- The user should see completion status for pending floors/checkpoints.

### Submission Rules

- A submission is created for a selected building, condition category, and floor.
- All required checklist points for that floor must be answered.
- Duplicate responses for the same checkpoint are not allowed.
- Responses that do not belong to the selected building/floor/category are not allowed.
- For the same user, building, condition, and floor, the system supports up to two inspections per day.
- After the second same-day inspection, the app should show that the daily inspection limit has been reached.

### Email Notifications

- Inspection results can be emailed to responsible users configured for checkpoint/floor ownership.
- Internal department emails can be kept in CC.
- A consolidated summary email can be sent to configured summary recipients.
- Email status, recipients, sent time, and errors are saved with the submission.

### Admin Setup

Admin setup includes:

- Building master.
- Floor master.
- Condition category master.
- Checkpoint master.
- Floor/checkpoint applicability.
- Responsible email mapping.
- Internal department email mapping.
- User access management.
- Excel checklist import and preview.

## Mobile App Screen Suggestions

- Login / access screen.
- Inspection home or direct inspection start screen.
- Building selector.
- Condition/category selector.
- Floor selector.
- Checklist screen.
- Live camera capture screen.
- Photo preview/retake screen.
- Submission progress screen.
- Submission success/error screen.
- Admin dashboard, only for admin users.
- Master setup screens, only for admin users.

## Important Product Rule

For inspection proof, only live photos captured from the camera are allowed.

No gallery upload, no clipboard paste, no copied image, and no file import should be available anywhere in the mobile app.
