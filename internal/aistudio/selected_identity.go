package aistudio

import (
	"context"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"slices"
	"strconv"
	"strings"
	"sync"
	"time"

	"google.golang.org/protobuf/encoding/protowire"
)

const selectedAccountsURL = "https://accounts.google.com/ListAccounts?gpsia=1&source=ChromiumBrowser&json=standard&laf=b64bin"

// SelectedIdentity is an account selector, never a subscription claim.
type SelectedIdentity struct {
	Email    string `json:"email"`
	GaiaID   string `json:"gaia_id"`
	AuthUser int    `json:"authuser"`
}

type selectedAccount struct {
	Email, GaiaID              string
	Valid, SignedOut, Verified bool
}

func (s StorageState) SelectedIdentity() (*SelectedIdentity, error) {
	raw, ok := s.extra["xiaoguofan_identity"]
	if !ok {
		return nil, nil
	}
	var id SelectedIdentity
	if err := json.Unmarshal(raw, &id); err != nil {
		return nil, errors.New("账号选择信息格式无效，请重新导出")
	}
	if id.AuthUser < 0 || id.AuthUser > 99 || !strings.Contains(id.Email, "@") || len(id.Email) > 254 || len(id.GaiaID) < 1 || len(id.GaiaID) > 32 {
		return nil, errors.New("账号选择信息无效，请重新导出")
	}
	for _, c := range id.GaiaID {
		if c < '0' || c > '9' {
			return nil, errors.New("Google 身份标识无效，请重新导出")
		}
	}
	return &id, nil
}

func decodeSelectedAccounts(raw []byte) ([]selectedAccount, error) {
	text := strings.TrimSpace(string(raw))
	var quoted string
	if json.Unmarshal([]byte(text), &quoted) == nil {
		text = quoted
	}
	data, err := base64.StdEncoding.DecodeString(text)
	if err != nil {
		data, err = base64.RawStdEncoding.DecodeString(text)
	}
	if err != nil {
		return nil, errors.New("无法解析 Google 账号列表，请重新导出")
	}
	rows := []selectedAccount{}
	for len(data) > 0 {
		num, typ, n := protowire.ConsumeTag(data)
		if n < 0 {
			return nil, errors.New("无效账号列表")
		}
		data = data[n:]
		if num != 1 || typ != protowire.BytesType {
			return nil, errors.New("账号列表协议已变化")
		}
		value, n := protowire.ConsumeBytes(data)
		if n < 0 {
			return nil, errors.New("无效账号列表")
		}
		data = data[n:]
		row := selectedAccount{Valid: true, Verified: true}
		for len(value) > 0 {
			field, wire, k := protowire.ConsumeTag(value)
			if k < 0 {
				return nil, errors.New("无效账号字段")
			}
			value = value[k:]
			switch {
			case (field == 3 || field == 10) && wire == protowire.BytesType:
				v, k := protowire.ConsumeBytes(value)
				if k < 0 {
					return nil, errors.New("无效账号字段")
				}
				value = value[k:]
				if field == 3 {
					row.Email = strings.ToLower(string(v))
				} else {
					row.GaiaID = string(v)
				}
			case (field == 9 || field == 14 || field == 15) && wire == protowire.VarintType:
				v, k := protowire.ConsumeVarint(value)
				if k < 0 {
					return nil, errors.New("无效账号状态")
				}
				value = value[k:]
				if field == 9 {
					row.Valid = v != 0
				}
				if field == 14 {
					row.SignedOut = v != 0
				}
				if field == 15 {
					row.Verified = v != 0
				}
			default:
				k := protowire.ConsumeFieldValue(field, wire, value)
				if k < 0 {
					return nil, errors.New("无效账号字段")
				}
				value = value[k:]
			}
		}
		rows = append(rows, row)
		if len(rows) > 100 {
			return nil, errors.New("账号列表过长")
		}
	}
	return rows, nil
}

func matchSelectedIdentity(rows []selectedAccount, id SelectedIdentity) (string, error) {
	index := -1
	for i, row := range rows {
		if row.GaiaID == id.GaiaID && strings.EqualFold(row.Email, id.Email) && row.Valid && !row.SignedOut && row.Verified {
			if index >= 0 {
				return "", errors.New("Google 身份不唯一")
			}
			index = i
		}
	}
	if index < 0 {
		return "", errors.New("登录会话与所选 Google 账号不匹配，请重新导出")
	}
	return strconv.Itoa(index), nil
}

// selectedIdentityCache shares only verified mappings for the same identity and
// Google session credentials. Cookie rotation and localStorage updates do not
// cause another ListAccounts request. Nothing is persisted as a trusted mapping.
var selectedIdentityCache = struct {
	sync.Mutex
	entries map[[32]byte]*selectedIdentityResolution
}{entries: make(map[[32]byte]*selectedIdentityResolution)}

type selectedIdentityResolution struct {
	done     chan struct{}
	authUser string
	err      error
}

func selectedIdentityKey(state StorageState) [32]byte {
	var credentials []string
	now := float64(time.Now().UnixNano()) / 1e9
	for _, cookie := range state.Cookies {
		if !cookieDomainMatches(cookie.Domain, "accounts.google.com") && !cookieDomainMatches(cookie.Domain, "myaccount.google.com") {
			continue
		}
		switch cookie.Name {
		case "SID", "HSID", "SSID", "APISID", "SAPISID", "__Secure-1PSID", "__Secure-3PSID", "__Secure-1PAPISID", "__Secure-3PAPISID", "OSID", "LSID", "ACCOUNT_CHOOSER", "__Host-GAPS":
			if cookie.Value != "" && (cookie.Expires < 0 || cookie.Expires > now) {
				encoded, _ := json.Marshal([]string{cookie.Domain, cookie.Path, cookie.Name, cookie.Value, strconv.FormatBool(cookie.Secure), cookie.PartitionKey})
				credentials = append(credentials, string(encoded))
			}
		}
	}
	slices.Sort(credentials)
	encoded, _ := json.Marshal([]any{credentials, state.extra["xiaoguofan_identity"], state.extra["xiaoguofan_identity_verification"]})
	return sha256.Sum256(encoded)
}

// InvalidateSelectedAuthUser discards the mapping after an authentication error.
// The next request rechecks the same credentials instead of using a stale index.
func InvalidateSelectedAuthUser(state StorageState) {
	key := selectedIdentityKey(state)
	selectedIdentityCache.Lock()
	delete(selectedIdentityCache.entries, key)
	selectedIdentityCache.Unlock()
}

// ResolveSelectedAuthUser reuses the mapping until session credentials change or
// authentication fails. Concurrent callers share the initial identity lookup.
func ResolveSelectedAuthUser(ctx context.Context, client *http.Client, state StorageState, fallback ...int) (string, error) {
	id, err := state.SelectedIdentity()
	if err != nil {
		return "", err
	}
	if id == nil {
		return VerifySelectedAuthUser(ctx, client, state, fallback...)
	}
	if err := ctx.Err(); err != nil {
		return "", err
	}
	key := selectedIdentityKey(state)
	selectedIdentityCache.Lock()
	if resolved := selectedIdentityCache.entries[key]; resolved != nil {
		selectedIdentityCache.Unlock()
		select {
		case <-ctx.Done():
			return "", ctx.Err()
		case <-resolved.done:
			return resolved.authUser, resolved.err
		}
	}
	// Bound memory across imported/replaced accounts; eviction only causes a recheck.
	if len(selectedIdentityCache.entries) >= 1024 {
		clear(selectedIdentityCache.entries)
	}
	resolved := &selectedIdentityResolution{done: make(chan struct{})}
	selectedIdentityCache.entries[key] = resolved
	selectedIdentityCache.Unlock()
	resolved.authUser, resolved.err = VerifySelectedAuthUser(ctx, client, state, fallback...)
	selectedIdentityCache.Lock()
	if resolved.err != nil && selectedIdentityCache.entries[key] == resolved {
		delete(selectedIdentityCache.entries, key)
	}
	close(resolved.done)
	selectedIdentityCache.Unlock()
	return resolved.authUser, resolved.err
}

// VerifySelectedAuthUser verifies email+Gaia against Google's current session.
// Use at credential import; never trust the saved numeric account position.
func VerifySelectedAuthUser(ctx context.Context, client *http.Client, state StorageState, fallback ...int) (string, error) {
	id, err := state.SelectedIdentity()
	if err != nil {
		return "", err
	}
	if id == nil {
		// Upstream Cookie imports may select a session without fork metadata.
		// An explicit identity is always verified and never uses this fallback.
		if len(fallback) > 0 {
			if fallback[0] < 0 || fallback[0] > 99 {
				return "", errors.New("Google 账号会话位置无效")
			}
			return strconv.Itoa(fallback[0]), nil
		}
		return "0", nil
	}
	ctx, cancel := context.WithTimeout(ctx, 30*time.Second)
	defer cancel()
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, selectedAccountsURL, strings.NewReader(" "))
	if err != nil {
		return "", err
	}
	cookie, err := state.CookieHeader(selectedAccountsURL, time.Now())
	if err != nil {
		return "", err
	}
	req.Header.Set("Cookie", cookie)
	req.Header.Set("User-Agent", publicDiscoveryUserAgent)
	req.Header.Set("Content-Type", "application/x-www-form-urlencoded")
	req.Header.Set("Origin", "https://www.google.com")
	req.Header.Set("Referer", aiStudioOrigin+"/")
	isolated := *client
	isolated.Jar = nil
	isolated.CheckRedirect = func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }
	res, err := isolated.Do(req)
	if err != nil {
		return "", errors.New("无法核对 Google 登录身份，请稍后重试")
	}
	defer res.Body.Close()
	if res.StatusCode != 200 {
		return "", fmt.Errorf("Google 身份核对返回 HTTP %d，请重新导出", res.StatusCode)
	}
	raw, err := io.ReadAll(io.LimitReader(res.Body, 100001))
	if err != nil || len(raw) > 100000 {
		return "", errors.New("Google 身份响应无效")
	}
	rows, err := decodeSelectedAccounts(raw)
	if err != nil {
		return "", err
	}
	if len(rows) == 0 {
		return resolveMyAccountDefault(ctx, &isolated, state, *id)
	}
	return matchSelectedIdentity(rows, *id)
}
