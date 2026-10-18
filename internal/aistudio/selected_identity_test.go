package aistudio

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"io"
	"net/http"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestSelectedIdentityListAccountIndexAndDrift(t *testing.T) {
	// Chromium ListAccountsResponse: field 1 accounts; account 3=email, 9=valid, 10=gaia.
	account := func(email, gaia string) []byte {
		b := []byte{26, byte(len(email))}
		b = append(b, []byte(email)...)
		b = append(b, 72, 1, 82, byte(len(gaia)))
		return append(b, []byte(gaia)...)
	}
	a, b := account("first@example.com", "111"), account("pro@example.com", "222")
	raw := append([]byte{10, byte(len(a))}, a...)
	raw = append(raw, 10, byte(len(b)))
	raw = append(raw, b...)
	rows, err := decodeSelectedAccounts([]byte(base64.StdEncoding.EncodeToString(raw)))
	if err != nil {
		t.Fatal(err)
	}
	id := SelectedIdentity{Email: "pro@example.com", GaiaID: "222", AuthUser: 1}
	index, err := matchSelectedIdentity(rows, id)
	if err != nil || index != "1" {
		t.Fatalf("index=%s err=%v", index, err)
	}
	index, err = matchSelectedIdentity([]selectedAccount{rows[1], rows[0]}, id)
	if err != nil || index != "0" {
		t.Fatal("must resolve current index, not trust saved index")
	}
	id.Email = "wrong@example.com"
	if _, err = matchSelectedIdentity(rows, id); err == nil {
		t.Fatal("identity mismatch accepted")
	}
	rows[1].Valid = false
	id.Email = "pro@example.com"
	if _, err = matchSelectedIdentity(rows, id); err == nil {
		t.Fatal("invalid session accepted")
	}
}

type identityRoundTripper func(*http.Request) (*http.Response, error)

func (f identityRoundTripper) RoundTrip(r *http.Request) (*http.Response, error) { return f(r) }
func TestSelectedIdentityVerifiedOverHTTPNotSavedIndex(t *testing.T) {
	var state StorageState
	json.Unmarshal([]byte(`{"cookies":[{"name":"SID","value":"fixture","domain":".google.com","path":"/","expires":-1}],"origins":[],"xiaoguofan_identity":{"email":"pro@example.com","gaia_id":"222","authuser":0}}`), &state)
	email := "pro@example.com"
	id := "222"
	row := append([]byte{26, byte(len(email))}, []byte(email)...)
	row = append(row, 72, 1, 82, byte(len(id)))
	row = append(row, []byte(id)...)
	payload := base64.StdEncoding.EncodeToString(append([]byte{10, byte(len(row))}, row...))
	client := &http.Client{Transport: identityRoundTripper(func(r *http.Request) (*http.Response, error) {
		if r.URL.String() != selectedAccountsURL || r.Method != "POST" || r.Header.Get("Origin") != "https://www.google.com" || !strings.Contains(r.Header.Get("Cookie"), "SID=fixture") {
			t.Fatal("incorrect identity protocol")
		}
		return &http.Response{StatusCode: 200, Body: io.NopCloser(strings.NewReader(payload)), Header: http.Header{}}, nil
	})}
	got, err := ResolveSelectedAuthUser(context.Background(), client, state)
	if err != nil || got != "0" {
		t.Fatalf("%s %v", got, err)
	}
	payload = ""
	if _, err = ResolveSelectedAuthUser(context.Background(), client, state); err == nil {
		t.Fatal("lost identity accepted")
	}
}

func TestSelectedIdentityOrdinaryRPCAndCookiePersistence(t *testing.T) {
	var state StorageState
	json.Unmarshal([]byte(`{"cookies":[{"name":"SAPISID","value":"fixture","domain":".google.com","path":"/","expires":-1},{"name":"__Secure-1PAPISID","value":"fixture","domain":".google.com","path":"/","expires":-1},{"name":"__Secure-3PAPISID","value":"fixture","domain":".google.com","path":"/","expires":-1}],"origins":[],"xiaoguofan_identity":{"email":"pro@example.com","gaia_id":"222","authuser":0}}`), &state)
	path := filepath.Join(t.TempDir(), "state.json")
	if err := WriteStorageState(path, state); err != nil {
		t.Fatal(err)
	}
	account := &Account{ID: "fixture", StoragePath: path, StorageState: state, Config: AccountConfig{Enabled: true}, State: AccountReady}
	pool := NewAccountPool([]*Account{account}, 1)
	accountRow := func(email, id string) []byte {
		b := append([]byte{26, byte(len(email))}, []byte(email)...)
		b = append(b, 72, 1, 82, byte(len(id)))
		return append(b, []byte(id)...)
	}
	a, b := accountRow("first@example.com", "111"), accountRow("pro@example.com", "222")
	raw := append([]byte{10, byte(len(a))}, a...)
	raw = append(raw, 10, byte(len(b)))
	raw = append(raw, b...)
	payload := base64.StdEncoding.EncodeToString(raw)
	calls := 0
	client := &http.Client{Transport: identityRoundTripper(func(r *http.Request) (*http.Response, error) {
		body := payload
		if r.URL.Host != "accounts.google.com" {
			calls++
			if r.Header.Get("X-Goog-AuthUser") != "1" {
				t.Fatal("RPC used wrong account")
			}
			if r.Header.Get("Authorization") == "" {
				t.Fatal("missing signing")
			}
			body = "[]"
		}
		return &http.Response{StatusCode: 200, Body: io.NopCloser(strings.NewReader(body)), Header: http.Header{}}, nil
	})}
	transport := &MakerSuiteHTTPTransport{pool: pool, signer: NewSigner(), headers: ProtocolHeaderProviderFunc(func(context.Context, string) (http.Header, error) {
		h := http.Header{}
		for _, n := range []string{"User-Agent", "X-Goog-Api-Key", "X-Goog-Authuser", "X-User-Agent"} {
			h.Set(n, "fixture")
		}
		return h, nil
	}), now: time.Now, clients: map[string]*http.Client{"": client}}
	response, err := transport.Do(context.Background(), RPCRequest{AccountID: "fixture", URL: "https://alkalimakersuite-pa.clients6.google.com/GetUserBenefits", Method: "GetUserBenefits", Body: []byte("[]")})
	if err != nil {
		t.Fatal(err)
	}
	response.Body.Close()
	if calls != 1 {
		t.Fatal("RPC not sent")
	}
	lease, _, err := pool.TryAcquireFor(context.Background(), AccountSelection{AccountID: "fixture"})
	if err != nil {
		t.Fatal(err)
	}
	if err = lease.ReplaceCookies(state.Cookies); err != nil {
		t.Fatal(err)
	}
	lease.Release()
	saved, err := LoadStorageState(path)
	if err != nil {
		t.Fatal(err)
	}
	id, err := saved.SelectedIdentity()
	if err != nil || id == nil || id.GaiaID != "222" {
		t.Fatal("identity lost during cookie refresh")
	}
	payload = ""
	if _, err = transport.Do(context.Background(), RPCRequest{AccountID: "fixture", URL: "https://alkalimakersuite-pa.clients6.google.com/GetUserBenefits", Method: "GetUserBenefits"}); err == nil {
		t.Fatal("missing identity accepted")
	}
	if calls != 1 {
		t.Fatal("failed identity sent RPC")
	}
}

func TestSelectedIdentityStorageAndValidation(t *testing.T) {
	var s StorageState
	if err := json.Unmarshal([]byte(`{"cookies":[],"origins":[],"xiaoguofan_identity":{"email":"pro@example.com","gaia_id":"222","authuser":1}}`), &s); err != nil {
		t.Fatal(err)
	}
	id, err := s.SelectedIdentity()
	if err != nil || id == nil || id.GaiaID != "222" {
		t.Fatalf("%v %v", id, err)
	}
	if _, err = decodeSelectedAccounts([]byte("not base64")); err == nil {
		t.Fatal("malformed wire accepted")
	}
}
