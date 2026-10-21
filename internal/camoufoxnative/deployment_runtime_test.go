package camoufoxnative

import (
	"context"
	"os"
	"strings"
	"testing"
	"time"

	"github.com/gorilla/websocket"
)

// Opt-in deployment probe: no Google requests, no account or cookie files.
func TestDeploymentOfflineBrowserRuntime(t *testing.T) {
	if os.Getenv("XIAOGUOFAN_RUNTIME_PROBE") != "1" {
		t.Skip("real browser deployment probe is opt-in")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 55*time.Second)
	defer cancel()
	executable, err := FindExecutable(ctx, "")
	if err != nil {
		t.Fatal(err)
	}
	t.Log("EXECUTABLE_READY")
	if os.Getenv("XIAOGUOFAN_DOWNLOAD_ONLY") == "1" {
		return
	}
	fingerprint, err := buildCamoufoxConfig(camoufoxFirefoxMajor, "en-US", "America/New_York")
	if err != nil {
		t.Fatal(err)
	}
	process, endpoint, err := launchBrowser(ctx, Options{ExecutablePath: executable, Headless: true, Log: os.Stderr}, fingerprint)
	if err != nil {
		t.Fatal(err)
	}
	defer func() {
		if err := process.Close(); err != nil {
			t.Error(err)
		}
	}()
	t.Log("BROWSER_LAUNCHED", endpoint)
	connection, _, err := websocket.DefaultDialer.DialContext(ctx, endpoint, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer connection.Close()
	connection.SetReadDeadline(time.Now().Add(35 * time.Second))
	t.Log("BIDI_CONNECTED")
	client := newBiDiClient(connection)
	if _, err = client.command(ctx, "session.new", map[string]any{"capabilities": map[string]any{}}); err != nil {
		t.Fatal(err)
	}
	t.Log("SESSION_READY")
	result, err := client.command(ctx, "browsingContext.getTree", map[string]any{"maxDepth": 0})
	if err != nil {
		t.Fatal(err)
	}
	contexts, ok := result["contexts"].([]any)
	if !ok || len(contexts) == 0 {
		t.Fatal("missing context tree", result)
	}
	tab, ok := contexts[0].(map[string]any)["context"].(string)
	if !ok || tab == "" {
		t.Fatal("missing tab")
	}
	t.Log("TAB_READY")
	if _, err = client.command(ctx, "browsingContext.navigate", map[string]any{"context": tab, "url": "data:text/html,<h1>xiaoguofan-offline-probe</h1>", "wait": "complete"}); err != nil {
		t.Fatal(err)
	}
	t.Log("NAVIGATION_READY")
	result, err = client.command(ctx, "script.evaluate", map[string]any{"expression": "document.body.textContent", "target": map[string]any{"context": tab}, "awaitPromise": false})
	if err != nil {
		t.Fatal(err)
	}
	value, ok := result["result"].(map[string]any)
	if !ok || !strings.Contains(value["value"].(string), "xiaoguofan-offline-probe") {
		t.Fatal("offline evaluation failed")
	}
	t.Log("REAL_CAMOUFOX_VERSION_LAUNCH_BIDI_OFFLINE_EVAL_PASS")
}
