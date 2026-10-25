// Builds the admin page into Warden's wwwroot/admin: Warden serves it at /admin/ (Program.cs, UseStaticFiles).
import path from "node:path";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { defineConfig } from "vite";

export default defineConfig({
  base: "./",
  plugins: [react(), tailwindcss()],
  resolve: { alias: { "@": path.resolve(__dirname, "src") } },
  build: { outDir: "../src/Warden.Api/wwwroot/admin", emptyOutDir: true },
});
