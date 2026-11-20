package aistudio

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"io"
	"net/http"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
)

func TestSelectedIdentityCacheSharesLookupAndRechecksCredentials(t *testing.T) {
	var state StorageState
	if err := json.Unmarshal([]byte(`{"cookies":[{"name":"SID","value":"cache-fixture","domain":".google.com","path":"/","expires":-1}],"origins":[],"xiaoguofan_identity":{"email":"cache@example.com","gaia_id":"333","authuser":7}}`), &state); err != nil {
		t.Fatal(err)
	}
	InvalidateSelectedAuthUser(state)
	t.Cleanup(func() { InvalidateSelectedAuthUser(state) })
	email, gaia := "cache@example.com", "333"
	row := append([]byte{26, byte(len(email))}, []byte(email)...)
	row = append(row, 72, 1, 82, byte(len(gaia)))
	row = append(row, []byte(gaia)...)
	payload := base64.StdEncoding.EncodeToString(append([]byte{10, byte(len(row))}, row...))
	var calls atomic.Int32
	var invalid atomic.Bool
	client := &http.Client{Transport: identityRoundTripper(func(*http.Request) (*http.Response, error) {
		calls.Add(1)
		body := payload
		if invalid.Load() {
			body = "invalid"
		}
		return &http.Response{StatusCode: 200, Header: http.Header{}, Body: io.NopCloser(strings.NewReader(body))}, nil
	})}
	var group sync.WaitGroup
	for range 8 {
		group.Go(func() {
			got, err := ResolveSelectedAuthUser(context.Background(), client, state, 7)
			if err != nil || got != "0" {
				t.Errorf("mapped authuser=%q err=%v", got, err)
			}
		})
	}
	group.Wait()
	if calls.Load() != 1 {
		t.Fatalf("concurrent requests made %d identity lookups", calls.Load())
	}
	rotated := state
	rotated.Cookies = append(append([]StateCookie(nil), state.Cookies...), StateCookie{Name: "SIDCC", Value: "rotated", Domain: ".google.com", Path: "/", Expires: -1})
	if _, err := ResolveSelectedAuthUser(context.Background(), client, rotated); err != nil || calls.Load() != 1 {
		t.Fatal("routine rotation should reuse mapping", err, calls.Load())
	}
	changed := state
	changed.Cookies = append([]StateCookie(nil), state.Cookies...)
	changed.Cookies[0].Value = "replaced-session"
	t.Cleanup(func() { InvalidateSelectedAuthUser(changed) })
	if _, err := ResolveSelectedAuthUser(context.Background(), client, changed); err != nil || calls.Load() != 2 {
		t.Fatal("changed session should be checked", err, calls.Load())
	}
	otherIdentity := state
	otherIdentity.extra = map[string]json.RawMessage{"xiaoguofan_identity": json.RawMessage(`{"email":"other@example.com","gaia_id":"444","authuser":0}`)}
	if _, err := ResolveSelectedAuthUser(context.Background(), client, otherIdentity); err == nil || calls.Load() != 3 {
		t.Fatal("different identity must not reuse another account's mapping", err, calls.Load())
	}
	InvalidateSelectedAuthUser(state)
	invalid.Store(true)
	if _, err := ResolveSelectedAuthUser(context.Background(), client, state, 7); err == nil || calls.Load() != 4 {
		t.Fatal("invalidated identity must be checked without numeric fallback", err, calls.Load())
	}
	invalid.Store(false)
	if _, err := ResolveSelectedAuthUser(context.Background(), client, state); err != nil || calls.Load() != 5 {
		t.Fatal("failed lookup must not be cached", err, calls.Load())
	}
}
